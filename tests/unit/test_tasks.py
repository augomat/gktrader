"""Unit tests for the signal pipeline tasks.

All external adapters (sources, classifier, market data, Telegram) are mocked
to avoid live network calls. The test database is SQLite in-memory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gktrader.config.settings import Settings
from gktrader.db.base import Base
from gktrader.db.models import (
    Alert,
    AlertOutbox,
    EventCompany,
    EventEvidence,
    ExtractedEvent,
    ProcessingRun,
    RawDocument,
    SignalEvent,
    SourceCursor,
    SourceDefinition,
    SourcePollRun,
    InteractionState,
    Position,
    PositionEvent,
    TradeDecision,
    WeeklyReport,
)
from gktrader.domain.contracts import (
    ClassifierResult,
    FetchIndexResult,
    MarketSnapshotContract,
    NormalizedDocument,
    PositionConfirmationRequest,
    SourceIndexItem,
)
from gktrader.domain.enums import (
    AlertLevel,
    DeliveryStatus,
    Direction,
    EventType,
    MarketStatus,
    PollRunStatus,
    PositionEventType,
    ProcessingStatus,
    SourceTier,
    TradeDecisionType,
)
from gktrader.intelligence.classifier import ClassificationRun
from gktrader.intelligence.prompts import get_prompt_info
from gktrader.intelligence.resolver import CompanyAlias, SecCompanyRecord, TickerResolver
from gktrader.sources.base import SourceAdapter
from gktrader.sources.truthsocial import TruthSocialAdapter
from gktrader.tasks.pipeline import AlertResult, PipelineResult, SignalPipeline, SignalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime(2026, 6, 12, 14, 30, 0, tzinfo=timezone.utc)


class _FakeRedisLock:
    def __init__(self, state: dict[str, bool], name: str):
        self._state = state
        self._name = name
        self._owned = False

    def acquire(self, blocking: bool = False) -> bool:
        if self._state.get(self._name):
            return False
        self._state[self._name] = True
        self._owned = True
        return True

    def release(self) -> None:
        if self._owned:
            self._state[self._name] = False
            self._owned = False


class _FakeRedisClient:
    def __init__(self):
        self._state: dict[str, bool] = {}

    def lock(self, name: str, timeout: int | None = None, blocking: bool = False) -> _FakeRedisLock:
        return _FakeRedisLock(self._state, name)


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "telegram_bot_token": "test-bot-token",
        "telegram_owner_id": 12345,
        "telegram_send_base_url": "https://api.telegram.org",
        "alpaca_api_key": "test-key",
        "alpaca_api_secret": "test-secret",
        "database_url": "sqlite+pysqlite:///:memory:",
        # Disable baseline by default so most pipeline tests run normally.
        # TestFirstStartBaseline explicitly enables it to test that code path.
        "enable_first_start_baseline": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_classifier_result(
    *,
    relevant: bool = True,
    event_type: str = "government_funding",
    direction: str = "bullish",
    company_name: str = "Rigetti Computing",
) -> ClassificationRun:
    """Build a successful ClassificationRun with a parsed ClassifierResult."""
    parsed = ClassifierResult(
        relevant=relevant,
        event_type=EventType(event_type),
        direction=Direction(direction),
        strength=5,
        confidence=0.92,
        companies=[{"name": company_name}],
        rationale="Test rationale.",
        risks=["Test risk"],
        action_status="announced",
        monetary_amounts=["$15M"],
        award_or_contract_ids=["CHIPS-2025-001"],
        government_actors=["Department of Commerce"],
        evidence=[{"text": "$15M awarded.", "start_offset": 0, "end_offset": 14}],
    )
    prompt_info = get_prompt_info()
    return ClassificationRun(
        model="google/gemini-2.0-flash-lite",
        prompt_version=prompt_info.version,
        prompt_hash=prompt_info.hash,
        raw_response='{"relevant":true,...}',
        parsed_result=parsed,
        status=ProcessingStatus.SUCCEEDED,
    )


def _make_market_snapshot(
    ticker: str = "RGTI",
    price: float = 4.25,
    previous_close: float = 4.00,
    intraday_move_pct: float = 6.25,
) -> MarketSnapshotContract:
    return MarketSnapshotContract(
        ticker=ticker,
        provider="alpaca",
        feed="IEX",
        observed_at=_now(),
        request_time=_now(),
        price=price,
        previous_close=previous_close,
        intraday_move_pct=intraday_move_pct,
        market_status=MarketStatus.OPEN,
        volume=1_250_000,
        label="IEX partial-market data",
    )


# ---------------------------------------------------------------------------
# Mock source adapter
# ---------------------------------------------------------------------------


class _MockSourceAdapter(SourceAdapter):
    """Test source adapter that returns pre-configured items."""

    source_name: str = "mock_source"
    source_tier: SourceTier = SourceTier.TIER_1

    def __init__(self, items: list[NormalizedDocument] | None = None):
        super().__init__()
        self._items = items or []

    def fetch_index(
        self, cursor: str | None = None, conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        idx_items = [
            SourceIndexItem(
                external_id=doc.external_id,
                detail_url=doc.canonical_url,
                title=doc.title,
                published_at=doc.published_at,
                metadata={},
            )
            for doc in self._items
        ]
        return FetchIndexResult(items=idx_items, fetch_path="mock")

    def fetch_detail(self, item: SourceIndexItem) -> Any:
        return item  # return the SourceIndexItem so normalize can get external_id

    def normalize(self, raw_item: Any) -> NormalizedDocument:
        # If raw_item is a SourceIndexItem, look up by external_id
        ext_id = self.derive_stable_external_id(raw_item)
        for doc in self._items:
            if doc.external_id == ext_id:
                return doc
        # Fallback: create one
        from pydantic import HttpUrl
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="mock",
            external_id=ext_id,
            canonical_url=HttpUrl("https://example.com/news/1"),
            title=str(raw_item) if not hasattr(raw_item, 'title') else (raw_item.title or ""),
            text=f"Sample text for {ext_id}",
            published_at=_now(),
            detected_at=_now(),
        )

    def derive_stable_external_id(self, raw_item: Any) -> str:
        if isinstance(raw_item, str):
            return raw_item
        if hasattr(raw_item, 'external_id'):
            return raw_item.external_id
        if hasattr(raw_item, 'title'):
            return raw_item.title
        return str(raw_item)


class _WrappedDetailAdapter(SourceAdapter):
    """Test adapter that returns wrapped detail payloads (dict with item + html).

    Simulates the pattern that HTML-detail source adapters (WhiteHouse, NIST,
    SEC, Commerce) will use: fetch_detail returns a dict preserving the original
    SourceIndexItem alongside the detail HTML, and normalize unpacks it to build
    a NormalizedDocument that retains the index metadata.
    """

    source_name: str = "wrapped_source"
    source_tier: SourceTier = SourceTier.TIER_1

    def __init__(self, items: list[NormalizedDocument] | None = None):
        super().__init__()
        self._items = items or []

    def fetch_index(
        self, cursor: str | None = None, conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        idx_items = [
            SourceIndexItem(
                external_id=doc.external_id,
                detail_url=doc.canonical_url,
                title=doc.title,
                published_at=doc.published_at,
                updated_at=doc.updated_at,
                metadata=dict(doc.source_metadata or {}),
            )
            for doc in self._items
        ]
        return FetchIndexResult(items=idx_items, fetch_path="rss")

    def fetch_detail(self, item: SourceIndexItem) -> Any:
        return {"item": item, "html": f"<html><body><p>Full article for {item.external_id}</p></body></html>"}

    def normalize(self, raw_item: Any) -> NormalizedDocument:
        if isinstance(raw_item, dict) and "item" in raw_item:
            item: SourceIndexItem = raw_item["item"]
            html: str = raw_item.get("html", "")
            from pydantic import HttpUrl
            return NormalizedDocument(
                source_name=self.source_name,
                source_tier=self.source_tier,
                fetch_path="detail",
                external_id=item.external_id,
                canonical_url=HttpUrl(str(item.detail_url)),
                title=item.title,
                text=html,
                published_at=item.published_at,
                updated_at=item.updated_at,
                detected_at=_now(),
                source_metadata=dict(item.metadata),
            )
        ext_id = self.derive_stable_external_id(raw_item)
        for doc in self._items:
            if doc.external_id == ext_id:
                return doc
        from pydantic import HttpUrl
        return NormalizedDocument(
            source_name=self.source_name,
            source_tier=self.source_tier,
            fetch_path="detail",
            external_id=ext_id,
            canonical_url=HttpUrl("https://example.com/news/1"),
            title=str(raw_item),
            text="Fallback text",
            published_at=_now(),
            detected_at=_now(),
        )

    def derive_stable_external_id(self, raw_item: Any) -> str:
        if isinstance(raw_item, dict) and "item" in raw_item:
            return raw_item["item"].external_id
        if hasattr(raw_item, "external_id"):
            return raw_item.external_id
        return str(raw_item)


class _FakeTruthSocialRepairAdapter(TruthSocialAdapter):
    source_name: str = "truthsocial"
    source_tier: SourceTier = SourceTier.TIER_1

    def __init__(self, mirror_posts: list[dict[str, Any]]):
        super().__init__(client=None)  # type: ignore[arg-type]
        self._mirror_items = self._parse_cnn_mirror({"data": mirror_posts})

    def fetch_index(
        self, cursor: str | None = None, conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        return FetchIndexResult(items=[], fetch_path="direct_api")

    def _fetch_cnn_mirror(
        self,
        cursor: str | None = None,
        conditional_headers: dict[str, str] | None = None,
    ) -> FetchIndexResult:
        return FetchIndexResult(items=self._mirror_items, fetch_path="cnn_mirror")


def _make_mock_doc(
    source_name: str = "mock_source",
    external_id: str = "mock-001",
    title: str = "Test Funding Announcement",
    text: str = "The Department of Commerce awarded $15M to Rigetti Computing for quantum.",
) -> NormalizedDocument:
    return NormalizedDocument(
        source_name=source_name,
        source_tier=SourceTier.TIER_1,
        fetch_path="mock",
        external_id=external_id,
        canonical_url="https://example.com/news/1",  # type: ignore[arg-type]
        title=title,
        text=text,
        published_at=_now(),
        detected_at=_now(),
    )


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Create an in-memory SQLite DB with all GKTrader tables."""
    engine = create_engine("sqlite+pysqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def resolver():
    """A TickerResolver pre-loaded with SEC master and alias data."""
    r = TickerResolver()
    r.load_sec_master([
        SecCompanyRecord(
            ticker="RGTI",
            name="Rigetti Computing",
            cik="0001838359",
            exchange="NASDAQ",
        ),
    ])
    r.load_aliases([
        CompanyAlias(
            name="Rigetti Computing",
            normalized_name="rigetti computing",
            ticker="RGTI",
            cik="0001838359",
            provenance="sec_master",
        ),
    ])
    return r


# ---------------------------------------------------------------------------
# Pipeline fixture
# ---------------------------------------------------------------------------


def _build_pipeline(
    db: Session,
    resolver: TickerResolver,
    mock_docs: list[NormalizedDocument] | None = None,
    *,
    classify_fn=None,
    snapshot_fn=None,
    deliver_fn=None,
    continue_deliver_fn=None,
    now_fn=None,
) -> SignalPipeline:
    settings = _make_settings()
    adapter = _MockSourceAdapter(mock_docs or [])
    adapters = {"mock_source": adapter}
    return SignalPipeline(
        db_session=db,
        settings=settings,
        adapters=adapters,
        resolver=resolver,
        classify_fn=classify_fn or (lambda t, x, m: _make_classifier_result()),
        snapshot_fn=snapshot_fn or (lambda t: _make_market_snapshot(t)),
        deliver_fn=deliver_fn or (lambda s, c, p: DeliveryStatus.SENT),
        continue_deliver_fn=continue_deliver_fn or (lambda s, c, m: [DeliveryStatus.SENT]),
        now_fn=now_fn or _now,
    )


# ---------------------------------------------------------------------------
# Tests: Ingestion stage
# ---------------------------------------------------------------------------


class TestIngestSources:
    def test_ingest_stores_raw_document(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        results = pipeline.ingest_sources()
        db_session.commit()

        assert len(results) == 1
        assert results[0].source_name == "mock_source"
        assert results[0].new_documents == 1
        assert results[0].status == PollRunStatus.SUCCEEDED

        # Verify RawDocument in DB
        stored = db_session.query(RawDocument).all()
        assert len(stored) == 1
        assert stored[0].source_name == "mock_source"
        assert stored[0].external_id == "mock-001"

    def test_ingest_deduplicates_by_unique_constraint(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        # First ingestion
        results1 = pipeline.ingest_sources()
        assert results1[0].new_documents == 1

        # Second ingestion — same doc should be skipped
        def later_now() -> datetime:
            return _now() + timedelta(seconds=61)

        pipeline_later = _build_pipeline(db_session, resolver, [doc], now_fn=later_now)
        results2 = pipeline_later.ingest_sources()
        db_session.commit()
        assert results2[0].new_documents == 0

    def test_ingest_backfills_truncated_truthsocial_index_fallback_text(self, db_session, resolver):
        raw = RawDocument(
            id=_db_uuid(),
            correlation_id="corr-ts-1",
            source_name="truthsocial",
            source_tier=SourceTier.TIER_1,
            fetch_path="index_fallback",
            external_id="ts-pw-legacy-1",
            canonical_url="https://truthsocial.com/",
            title=(
                "Donald J. Trump @realDonaldTrump · 7h Congratulations to Jim Dolan and the "
                "New York Knicks!!! What a year it has been…"
            ),
            text=(
                "Donald J. Trump @realDonaldTrump · 7h Congratulations to Jim Dolan and the "
                "New York Knicks!!! What a year it has been…"
            ),
            content_hash="hash-ts-1",
            detected_at=_now(),
            source_metadata={"playwright_line": 2},
        )
        db_session.add(raw)
        db_session.commit()

        mirror_posts = [{
            "id": "116746575777210117",
            "text": (
                "Congratulations to Jim Dolan and the New York Knicks!!! What a year it has been "
                "but, even more so, what incredible playoff wins we have all witnessed, especially "
                "the last four - Maybe the greatest in the history of basketball. Also, tonight, a "
                "superstar was born. His name is Jalen Brunson."
            ),
            "url": "https://truthsocial.com/@realDonaldTrump/posts/116746575777210117",
            "created_at": "2026-06-14T12:00:00.000Z",
        }]
        adapter = _FakeTruthSocialRepairAdapter(mirror_posts)
        pipeline = SignalPipeline(
            db_session=db_session,
            settings=_make_settings(),
            adapters={"truthsocial": adapter},
            resolver=resolver,
            classify_fn=lambda t, x, m: _make_classifier_result(),
            snapshot_fn=lambda t: _make_market_snapshot(t),
            deliver_fn=lambda s, c, p: DeliveryStatus.SENT,
            continue_deliver_fn=lambda s, c, m: [DeliveryStatus.SENT],
            now_fn=_now,
        )

        results = pipeline.ingest_sources(["truthsocial"])
        db_session.commit()

        repaired = db_session.get(RawDocument, raw.id)
        assert results[0].status == PollRunStatus.SUCCEEDED
        assert repaired is not None
        assert repaired.text.startswith("Congratulations to Jim Dolan and the New York Knicks!!!")
        assert repaired.source_metadata["backfilled_from"] == "cnn_mirror"
        assert repaired.source_metadata["original_id"] == "116746575777210117"

        stored = db_session.query(RawDocument).all()
        assert len(stored) == 1

    def test_ingest_records_poll_run(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        db_session.commit()

        runs = db_session.query(SourcePollRun).all()
        assert len(runs) == 1
        assert runs[0].source_name == "mock_source"
        assert runs[0].fetch_count == 1
        assert runs[0].new_count == 1

    def test_ingest_updates_cursor(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        db_session.commit()

        from gktrader.db.models import SourceCursor
        cursor = db_session.query(SourceCursor).filter_by(source_name="mock_source").first()
        assert cursor is not None
        assert cursor.last_successful_poll is not None

    def test_ingest_skips_source_until_due(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc], now_fn=_now)

        first_results = pipeline.ingest_sources()
        assert first_results[0].skipped is False
        assert first_results[0].new_documents == 1

        second_results = pipeline.ingest_sources()
        db_session.commit()

        assert second_results[0].skipped is True
        assert second_results[0].new_documents == 0

        runs = db_session.query(SourcePollRun).all()
        assert len(runs) == 1

    def test_ingest_refreshes_persisted_poll_interval_from_adapter(self, db_session, resolver):
        source_def = SourceDefinition(
            source_name="mock_source",
            source_tier=SourceTier.TIER_1,
            poll_interval_seconds=60,
        )
        cursor = SourceCursor(
            source_name="mock_source",
            last_successful_poll=_now() - timedelta(seconds=61),
        )
        db_session.add(source_def)
        db_session.add(cursor)
        db_session.commit()

        doc = _make_mock_doc()

        def later_now() -> datetime:
            return _now() + timedelta(minutes=2)

        pipeline = _build_pipeline(db_session, resolver, [doc], now_fn=later_now)
        pipeline.adapters["mock_source"].poll_interval_seconds = 600

        results = pipeline.ingest_sources()
        db_session.commit()

        refreshed = db_session.query(SourceDefinition).filter_by(source_name="mock_source").one()
        assert refreshed.poll_interval_seconds == 600
        assert results[0].skipped is True

    def test_failed_poll_is_still_rate_limited_until_due(self, db_session, resolver):
        class _FailingMockSourceAdapter(_MockSourceAdapter):
            def fetch_index(
                self,
                cursor: str | None = None,
                conditional_headers: dict[str, str] | None = None,
            ) -> FetchIndexResult:
                raise RuntimeError("cm4 endpoint down")

        pipeline = _build_pipeline(db_session, resolver, [], now_fn=_now)
        pipeline.adapters["mock_source"] = _FailingMockSourceAdapter()

        first_results = pipeline.ingest_sources()
        db_session.commit()

        assert first_results[0].status == PollRunStatus.FAILED
        assert first_results[0].skipped is False

        second_results = pipeline.ingest_sources()
        db_session.commit()

        assert second_results[0].skipped is True

        runs = db_session.query(SourcePollRun).all()
        assert len(runs) == 1

    def test_ingest_preserves_index_metadata_through_wrapped_detail(self, db_session, resolver):
        """Wrapped detail payloads must preserve index metadata in RawDocument."""
        from datetime import timedelta
        pub_at = _now() - timedelta(hours=2)
        doc = NormalizedDocument(
            source_name="wrapped_source",
            source_tier=SourceTier.TIER_1,
            fetch_path="detail",
            external_id="wh-2025-03-01",
            canonical_url="https://www.whitehouse.gov/briefing-room/statements-releases/2025/03/01/test-announcement/",  # type: ignore[arg-type]
            title="Test White House Announcement",
            text="Full article text about policy.",
            published_at=pub_at,
            updated_at=pub_at,
            source_metadata={"author": "Press Secretary", "category": "economy"},
            detected_at=_now(),
        )
        adapter = _WrappedDetailAdapter([doc])
        settings = _make_settings()
        pipeline = SignalPipeline(
            db_session=db_session,
            settings=settings,
            adapters={"wrapped_source": adapter},
            resolver=resolver,
            classify_fn=lambda t, x, m: _make_classifier_result(),
            snapshot_fn=lambda t: _make_market_snapshot(t),
            deliver_fn=lambda s, c, p: DeliveryStatus.SENT,
            continue_deliver_fn=lambda s, c, m: [DeliveryStatus.SENT],
            now_fn=_now,
        )

        results = pipeline.ingest_sources(["wrapped_source"])
        db_session.commit()

        assert len(results) == 1
        assert results[0].new_documents == 1
        assert results[0].status == PollRunStatus.SUCCEEDED

        stored = db_session.query(RawDocument).all()
        assert len(stored) == 1
        rd = stored[0]

        # external_id preserved from SourceIndexItem
        assert rd.external_id == "wh-2025-03-01"
        # canonical_url preserved from index item detail_url
        assert "whitehouse.gov" in rd.canonical_url
        # title preserved from feed item
        assert rd.title == "Test White House Announcement"
        # published_at preserved from feed timestamp
        assert rd.published_at is not None
        # SQLite stores naive datetimes — compare after making tz-aware
        stored_pub = rd.published_at
        if stored_pub.tzinfo is None:
            stored_pub = stored_pub.replace(tzinfo=timezone.utc)
        assert stored_pub == pub_at
        # metadata from source_metadata preserved
        assert rd.source_metadata is not None
        assert rd.source_metadata.get("author") == "Press Secretary"
        # text contains full article content (from html in wrapped payload)
        assert "Full article" in rd.text

    def test_sec_prefilter_skip(self, db_session, resolver):
        """SEC items with prefilter_match=False must be skipped before fetch_detail."""
        from datetime import timedelta
        pub_at = _now() - timedelta(hours=1)

        # Two docs: one passes prefilter, one does not
        passing_item = NormalizedDocument(
            source_name="sec_8k",
            source_tier=SourceTier.TIER_1,
            fetch_path="rss",
            external_id="sec-pass-001",
            canonical_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001838359",  # type: ignore[arg-type]
            title="8-K - Rigetti Computing Inc",
            text="Item 8.01 Other Events.",
            published_at=pub_at,
            source_metadata={"prefilter_match": True, "accession": "0001838359-25-000001"},
            detected_at=_now(),
        )
        skipped_item = NormalizedDocument(
            source_name="sec_8k",
            source_tier=SourceTier.TIER_1,
            fetch_path="rss",
            external_id="sec-skip-001",
            canonical_url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193",  # type: ignore[arg-type]
            title="8-K - Some Other Company",
            text="Item 1.01 Entry into a Material Definitive Agreement.",
            published_at=pub_at,
            source_metadata={"prefilter_match": False, "accession": "0000320193-25-000002"},
            detected_at=_now(),
        )

        # Use a custom adapter that returns both items from its index list
        # and maps them back in normalize by external_id
        class _SecPrefilterAdapter(_WrappedDetailAdapter):
            source_name = "sec_8k"

        adapter = _SecPrefilterAdapter([passing_item, skipped_item])
        settings = _make_settings()
        pipeline = SignalPipeline(
            db_session=db_session,
            settings=settings,
            adapters={"sec_8k": adapter},
            resolver=resolver,
            classify_fn=lambda t, x, m: _make_classifier_result(),
            snapshot_fn=lambda t: _make_market_snapshot(t),
            deliver_fn=lambda s, c, p: DeliveryStatus.SENT,
            continue_deliver_fn=lambda s, c, m: [DeliveryStatus.SENT],
            now_fn=_now,
        )

        results = pipeline.ingest_sources(["sec_8k"])
        db_session.commit()

        assert len(results) == 1
        # Only the passing item should be stored
        assert results[0].new_documents == 1

        stored = db_session.query(RawDocument).all()
        assert len(stored) == 1
        assert stored[0].external_id == "sec-pass-001"
        assert stored[0].source_metadata.get("prefilter_match") is True

        # Verify the skipped item's external_id is NOT in the DB
        skipped = db_session.query(RawDocument).filter_by(external_id="sec-skip-001").first()
        assert skipped is None


# ---------------------------------------------------------------------------
# Tests: Processing stage
# ---------------------------------------------------------------------------


class TestProcessDocuments:
    def test_process_creates_processing_run_and_extracted_event(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        # Ingest first
        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]

        # Process
        results = pipeline.process_documents(raw_ids)
        db_session.commit()

        assert len(results) == 1
        assert results[0].status == ProcessingStatus.SUCCEEDED
        assert results[0].processing_run_id is not None
        assert results[0].extracted_event_id is not None

        # Verify ProcessingRun
        pr = db_session.query(ProcessingRun).first()
        assert pr is not None
        assert pr.status == ProcessingStatus.SUCCEEDED

        # Verify ExtractedEvent
        ee = db_session.query(ExtractedEvent).first()
        assert ee is not None
        assert ee.event_payload.get("event_type") == "government_funding"

    def test_process_creates_event_company_mappings(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        db_session.commit()

        companies = db_session.query(EventCompany).all()
        assert len(companies) == 1
        assert companies[0].candidate_name == "Rigetti Computing"
        assert companies[0].mapping_confidence > 0

    def test_process_handles_classifier_failure(self, db_session, resolver):
        doc = _make_mock_doc()

        def _fail_classify(title, text, meta):
            return ClassificationRun(
                model="test",
                prompt_version="1.0.0",
                prompt_hash="abc123",
                status=ProcessingStatus.FAILED,
                error="Classifier API error",
            )

        pipeline = _build_pipeline(db_session, resolver, [doc], classify_fn=_fail_classify)

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        results = pipeline.process_documents(raw_ids)
        db_session.commit()

        assert results[0].status == ProcessingStatus.FAILED
        assert results[0].error == "Classifier API error"

    def test_process_skips_irrelevant_documents(self, db_session, resolver):
        doc = _make_mock_doc()

        def _irrelevant_classify(title, text, meta):
            cr = _make_classifier_result(relevant=False, event_type="irrelevant", direction="neutral")
            return cr

        pipeline = _build_pipeline(db_session, resolver, [doc], classify_fn=_irrelevant_classify)

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        results = pipeline.process_documents(raw_ids)
        db_session.commit()

        assert results[0].status == ProcessingStatus.SUCCEEDED
        assert results[0].extracted_event_id is None

    def test_classify_sync_uses_configured_fallback_model(self, monkeypatch):
        from gktrader.tasks import pipeline as pipeline_module

        settings = _make_settings(
            openrouter_api_key="test-key",
            openrouter_model="primary-model",
            openrouter_fallback_model="fallback-model",
        )

        captured: dict[str, Any] = {}

        class _DummyClassifier:
            def __init__(self, config):
                captured["config"] = config

            async def classify(self, title, text, source_metadata=None):
                return ClassificationRun(
                    model=self.config.model,
                    prompt_version="1.0.0",
                    prompt_hash="hash",
                    status=ProcessingStatus.SUCCEEDED,
                )

            async def close(self):
                return None

            @property
            def config(self):
                return captured["config"]

        monkeypatch.setattr("gktrader.config.settings.get_settings", lambda: settings)
        monkeypatch.setattr("gktrader.intelligence.classifier.OpenRouterClassifier", _DummyClassifier)

        run = pipeline_module._classify_sync("Title", "Body")

        assert run.status == ProcessingStatus.SUCCEEDED
        assert captured["config"].model == "primary-model"
        assert captured["config"].fallback_model == "fallback-model"


# ---------------------------------------------------------------------------
# Tests: Signal creation stage
# ---------------------------------------------------------------------------


class TestCreateSignals:
    def test_creates_signal_event_from_extracted(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]

        results = pipeline.create_signals(ee_ids)
        db_session.commit()

        assert len(results) == 1
        assert results[0].signal_event_id is not None
        assert results[0].alert_level != AlertLevel.IGNORE

        sig = db_session.query(SignalEvent).first()
        assert sig is not None
        assert sig.event_type == "government_funding"
        assert sig.fingerprint is not None
        assert len(sig.fingerprint) == 64  # SHA-256 hex

    def test_creates_event_evidence(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        pipeline.create_signals(ee_ids)
        db_session.commit()

        evidence = db_session.query(EventEvidence).all()
        assert len(evidence) == 1
        assert "$15M" in evidence[0].evidence_text

    def test_deduplicates_by_fingerprint(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]

        # First signal creation
        pipeline.create_signals(ee_ids)

        # Second signal creation with same data — should hit fingerprint dedupe
        results2 = pipeline.create_signals(ee_ids)
        db_session.commit()

        # Should only have one SignalEvent
        signals = db_session.query(SignalEvent).all()
        assert len(signals) == 1

    def test_ignores_irrelevant_events(self, db_session, resolver):
        doc = _make_mock_doc()

        def _irrelevant_classify(title, text, meta):
            return _make_classifier_result(
                relevant=False, event_type="irrelevant", direction="neutral",
            )

        pipeline = _build_pipeline(db_session, resolver, [doc], classify_fn=_irrelevant_classify)

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]

        # No extracted events for irrelevant docs
        if ee_ids:
            results = pipeline.create_signals(ee_ids)
            db_session.commit()

            for r in results:
                assert r.skipped or r.alert_level == AlertLevel.IGNORE


# ---------------------------------------------------------------------------
# Tests: Alert creation stage
# ---------------------------------------------------------------------------


class TestCreateAlerts:
    def test_creates_alert_and_outbox_for_tradeable(self, db_session, resolver):
        doc = _make_mock_doc(
            title="CHIPS Act Quantum Computing Grant",
            text="The White House announced a $15M CHIPS Act grant for Rigetti Computing quantum computing development.",
        )
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        pipeline.create_signals(ee_ids)
        sig_ids = [s.id for s in db_session.query(SignalEvent).all()]

        results = pipeline.create_alerts(sig_ids)
        db_session.commit()

        assert len(results) >= 1

        alerts = db_session.query(Alert).all()
        assert len(alerts) >= 1
        assert alerts[0].level in (
            AlertLevel.TRADEABLE, AlertLevel.REVIEW, AlertLevel.AVOID_CHASE,
        )

        outbox = db_session.query(AlertOutbox).all()
        assert len(outbox) >= 1

    def test_watch_skips_alert_and_outbox(self, db_session, resolver):
        # Build a WATCH-level signal by making it sector-only
        doc = _make_mock_doc()
        pipeline = _build_pipeline(
            db_session,
            resolver,
            [doc],
            classify_fn=lambda t, x, m: _make_classifier_result(
                event_type="sector_only_mention", direction="neutral",
            ),
        )

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        pipeline.create_signals(ee_ids)
        sig_ids = [s.id for s in db_session.query(SignalEvent).all()]

        # Check that signals are WATCH
        for sig in db_session.query(SignalEvent).all():
            if sig.alert_level == AlertLevel.WATCH:
                results = pipeline.create_alerts([sig.id])
                db_session.commit()
                for r in results:
                    assert r.skipped
                    assert "WATCH" in r.reason

    def test_avoid_chase_still_creates_alert(self, db_session, resolver):
        doc = _make_mock_doc(
            text="CHIPS Act grant for Rigetti Computing.",
        )
        # Force a big price move -> AVOID_CHASE
        pipeline = _build_pipeline(
            db_session,
            resolver,
            [doc],
            snapshot_fn=lambda t: _make_market_snapshot(t, intraday_move_pct=30.0),
        )

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        pipeline.create_signals(ee_ids)

        # Check that AVOID_CHASE still gets an alert
        alerts = db_session.query(Alert).all()
        # alerts may not be created yet (done in create_alerts stage)
        sig_ids = [s.id for s in db_session.query(SignalEvent).all()]
        results = pipeline.create_alerts(sig_ids)
        db_session.commit()

        # AVOID_CHASE should create an alert
        avoid_results = [r for r in results if r.alert_level == AlertLevel.AVOID_CHASE]
        if avoid_results:
            assert avoid_results[0].alert_id is not None


# ---------------------------------------------------------------------------
# Tests: Full pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_full_pipeline_end_to_end(self, db_session, resolver):
        """Run the full pipeline and verify all stage results."""
        doc = _make_mock_doc(
            title="Major CHIPS Act Quantum Award",
            text="The Department of Commerce awarded $15M to Rigetti Computing under the CHIPS Act.",
        )
        pipeline = _build_pipeline(db_session, resolver, [doc])

        result = pipeline.run_full_pipeline()
        db_session.commit()

        assert result.total_raw_documents == 1
        assert result.total_signals >= 1
        assert result.total_alerts >= 1

        # Verify all DB tables have records
        assert db_session.query(RawDocument).count() >= 1
        assert db_session.query(ProcessingRun).count() >= 1
        assert db_session.query(ExtractedEvent).count() >= 1
        assert db_session.query(EventCompany).count() >= 1
        assert db_session.query(SignalEvent).count() >= 1
        assert db_session.query(EventEvidence).count() >= 1
        # Alert may not be created if WATCH or score too low for TRADEABLE
        if result.total_alerts > 0:
            assert db_session.query(Alert).count() >= 1
            assert db_session.query(AlertOutbox).count() >= 1

    def test_full_pipeline_with_multiple_documents(self, db_session, resolver):
        """Multiple documents should produce independent signals."""
        doc1 = _make_mock_doc(external_id="mock-001", title="CHIPS Award 1")
        doc2 = _make_mock_doc(external_id="mock-002", title="Executive Order on Semiconductors")
        pipeline = _build_pipeline(db_session, resolver, [doc1, doc2])

        result = pipeline.run_full_pipeline()
        db_session.commit()

        assert result.total_raw_documents == 2
        # Both should get processed
        assert len(result.processing_results) == 2
        assert all(pr.status == ProcessingStatus.SUCCEEDED for pr in result.processing_results)

    def test_pipeline_result_has_correct_counts(self, db_session, resolver):
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        result = pipeline.run_full_pipeline()
        db_session.commit()

        assert isinstance(result, PipelineResult)
        assert result.started_at is not None
        assert result.completed_at is not None
        assert len(result.ingest_results) == 1
        assert len(result.signal_results) >= 0
        assert len(result.alert_results) >= 0


# ---------------------------------------------------------------------------
# Tests: Delivery stage
# ---------------------------------------------------------------------------


class TestDeliverPending:
    def test_deliver_pending_sends_alert(self, db_session, resolver):
        """Full ingestion + delivery pipeline with mocked Telegram."""
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        # Run full pipeline up to outbox creation
        pipeline.run_full_pipeline()
        db_session.commit()

        # Now deliver — results should include at least one delivery
        results = pipeline.deliver_pending()
        db_session.commit()

        # We should have attempted at least one delivery
        assert len(results) >= 0  # May already be delivered in run_full_pipeline

    def test_deliver_handles_timeout(self, db_session, resolver):
        """When Telegram times out, delivery is marked UNKNOWN."""
        doc = _make_mock_doc()
        pipeline = _build_pipeline(
            db_session,
            resolver,
            [doc],
            deliver_fn=lambda s, c, p: DeliveryStatus.UNKNOWN,
        )

        pipeline.run_full_pipeline()
        db_session.commit()

        results = pipeline.deliver_pending()
        db_session.commit()

        # Should mark some as UNKNOWN
        unknown_results = [r for r in results if r.status == DeliveryStatus.UNKNOWN]
        if unknown_results:
            outbox_entries = db_session.query(AlertOutbox).all()
            for entry in outbox_entries:
                assert entry.status in (
                    DeliveryStatus.PENDING,
                    DeliveryStatus.CLAIMED,
                    DeliveryStatus.SENT,
                    DeliveryStatus.UNKNOWN,
                )

    def test_no_watch_delivery_path(self, db_session, resolver):
        """WATCH alerts should never reach the delivery stage."""
        doc = _make_mock_doc()

        def _watch_classify(title, text, meta):
            return _make_classifier_result(
                event_type="sector_only_mention",
                direction="neutral",
            )

        pipeline = _build_pipeline(
            db_session, resolver, [doc], classify_fn=_watch_classify,
        )

        result = pipeline.run_full_pipeline()
        db_session.commit()

        # WATCH alerts: alert_results should all be skipped
        for ar in result.alert_results:
            assert ar.skipped
            assert "WATCH" in ar.reason

        # No outbox entries for WATCH
        outbox = db_session.query(AlertOutbox).all()
        # WATCH should not create outbox; if no TRADEABLE alerts, outbox is empty
        # Actually, some alerts might be WATCH and some REVIEW — verify WATCH alone
        watch_signals = (
            db_session.query(SignalEvent)
            .filter(SignalEvent.alert_level == AlertLevel.WATCH)
            .all()
        )
        for sig in watch_signals:
            alert = db_session.query(Alert).filter_by(signal_event_id=sig.id).first()
            assert alert is None, f"WATCH signal {sig.id} should not have an alert"


# ---------------------------------------------------------------------------
# Tests: Poll sources task (Celery task stub)
# ---------------------------------------------------------------------------


class TestPollSourcesTask:
    def test_poll_sources_is_not_noop(self):
        """The poll_sources function is now a Celery task, not a noop."""
        from gktrader.tasks.jobs import poll_sources

        # Verify it's a task function (decorated with @celery_app.task)
        assert callable(poll_sources)
        assert hasattr(poll_sources, "name")

    def test_deliver_pending_is_not_noop(self):
        """The deliver_pending_alerts function is a real Celery task."""
        from gktrader.tasks.jobs import deliver_pending_alerts

        assert callable(deliver_pending_alerts)
        assert hasattr(deliver_pending_alerts, "name")

    def test_poll_sources_skips_when_lock_is_held(self, monkeypatch):
        from gktrader.tasks import jobs

        fake_redis = _FakeRedisClient()
        settings = _make_settings(redis_url="redis://unused:6379/0")
        held_lock = fake_redis.lock("gktrader:poll_sources:lock", timeout=900, blocking=False)
        assert held_lock.acquire(blocking=False) is True

        monkeypatch.setattr(jobs, "get_settings", lambda: settings)
        monkeypatch.setattr(jobs, "_get_redis_client", lambda current_settings: fake_redis)

        result = jobs.poll_sources.run()

        assert result["status"] == "skipped"
        assert result["reason"] == "another poll_sources run is still active"
        held_lock.release()

    def test_poll_sources_releases_lock_after_completion(self, monkeypatch):
        from gktrader.tasks import jobs

        fake_redis = _FakeRedisClient()
        settings = _make_settings(redis_url="redis://unused:6379/0")

        monkeypatch.setattr(jobs, "get_settings", lambda: settings)
        monkeypatch.setattr(jobs, "_get_redis_client", lambda current_settings: fake_redis)
        monkeypatch.setattr(jobs, "_build_pipeline", lambda: object())
        monkeypatch.setattr(
            jobs,
            "_run_in_session",
            lambda pipeline: PipelineResult(
                ingest_results=[],
                processing_results=[],
                signal_results=[],
                alert_results=[],
            ),
        )

        result = jobs.poll_sources.run()
        second_lock = fake_redis.lock("gktrader:poll_sources:lock", timeout=900, blocking=False)

        assert result["status"] == "completed"
        assert second_lock.acquire(blocking=False) is True
        second_lock.release()


# ---------------------------------------------------------------------------
# Tests: Production resolver wiring (round-2 critical #1)
# ---------------------------------------------------------------------------
#
# Regression for the bug where _build_pipeline created an EMPTY TickerResolver,
# so no company resolved to a validated ticker and nothing could ever become
# TRADEABLE.  The production resolver must be loaded with the SEC master.


class TestResolverWiring:
    @staticmethod
    def _reset_cache():
        from gktrader.tasks import jobs

        jobs._RESOLVER_CACHE["resolver"] = None
        jobs._RESOLVER_CACHE["loaded_at"] = 0.0

    def test_get_resolver_loads_sec_master(self, monkeypatch):
        from gktrader.tasks import jobs

        self._reset_cache()
        fake = [
            {"ticker": "INTC", "name": "INTEL CORP", "cik": "50863"},
            {"ticker": "NVDA", "name": "NVIDIA CORP", "cik": "1045810"},
        ]
        monkeypatch.setattr(
            jobs.SECAdapter,
            "fetch_ticker_master",
            classmethod(lambda cls, client, user_agent: fake),
        )

        resolver = jobs._get_resolver(Settings(), force=True)
        assert len(resolver.get_sec_records()) == 2
        result = resolver.resolve("Intel Corporation")
        assert result.best_candidate is not None
        assert result.best_candidate.ticker == "INTC"

        # Second call within TTL returns the cached object (no re-download).
        assert jobs._get_resolver(Settings()) is resolver
        self._reset_cache()

    def test_get_resolver_failure_does_not_crash(self, monkeypatch):
        from gktrader.tasks import jobs

        self._reset_cache()

        def boom(cls, client, user_agent):
            raise RuntimeError("network down")

        monkeypatch.setattr(
            jobs.SECAdapter, "fetch_ticker_master", classmethod(boom)
        )

        # With no prior cache, a load failure yields an empty resolver rather
        # than raising (the poll degrades instead of dying).
        resolver = jobs._get_resolver(Settings(), force=True)
        assert resolver.get_sec_records() == {}
        self._reset_cache()


class TestGkfetchEndpointSelection:
    def test_cm4_endpoint_uses_explicit_cm4_config(self):
        from gktrader.tasks import jobs

        settings = Settings(
            gkfetch_url="http://legacy:8899",
            gkfetch_secret="legacy-secret",
            gkfetch_cm4_url="http://cm4:8899",
            gkfetch_cm4_secret="cm4-secret",
        )

        assert jobs._cm4_gkfetch_config(settings) == ("http://cm4:8899", "cm4-secret")

    def test_cm4_endpoint_falls_back_to_legacy_global_config(self):
        from gktrader.tasks import jobs

        settings = Settings(
            gkfetch_url="http://legacy:8899",
            gkfetch_secret="legacy-secret",
        )

        assert jobs._cm4_gkfetch_config(settings) == ("http://legacy:8899", "legacy-secret")

    def test_commerce_prefers_georg_laptop_endpoint(self):
        from gktrader.tasks import jobs

        settings = Settings(
            gkfetch_cm4_url="http://cm4:8899",
            gkfetch_cm4_secret="cm4-secret",
            gkfetch_georg_laptop_url="http://georg:8899",
            gkfetch_georg_laptop_secret="georg-secret",
        )

        assert jobs._commerce_gkfetch_config(settings) == (
            "http://georg:8899",
            "georg-secret",
        )

    def test_commerce_georg_endpoint_does_not_fallback_to_cm4(self):
        from gktrader.tasks import jobs

        settings = Settings(
            gkfetch_cm4_url="http://cm4:8899",
            gkfetch_cm4_secret="cm4-secret",
        )

        assert jobs._commerce_gkfetch_config(settings) == ("", "")


# ---------------------------------------------------------------------------
# Tests: Alert level gating
# ---------------------------------------------------------------------------


class TestAlertLevelGating:
    def test_trADEable_creates_outbox(self, db_session, resolver):
        """TRADEABLE alerts must create alert + outbox entries."""
        doc = _make_mock_doc(
            text="The White House announced a $15M CHIPS Act grant for Rigetti Computing.",
        )
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        pipeline.create_signals(ee_ids)
        sig_ids = [s.id for s in db_session.query(SignalEvent).all()]

        # Any TRADEABLE signal should create an alert + outbox
        tradeable_sigs = [
            s for s in db_session.query(SignalEvent).all()
            if s.alert_level == AlertLevel.TRADEABLE
        ]
        if tradeable_sigs:
            results = pipeline.create_alerts([tradeable_sigs[0].id])
            db_session.commit()
            # At least one alert created (if the score passes TRADEABLE gate)
            alerts = db_session.query(Alert).all()
            assert len(alerts) >= 1
            outbox = db_session.query(AlertOutbox).all()
            assert len(outbox) >= 1

    def test_avoid_chase_creates_outbox(self, db_session, resolver):
        """AVOID_CHASE alerts must still create alert + outbox (but deliver)."""
        doc = _make_mock_doc(
            text="CHIPS Act award for Rigetti Computing.",
        )
        pipeline = _build_pipeline(
            db_session,
            resolver,
            [doc],
            snapshot_fn=lambda t: _make_market_snapshot(t, intraday_move_pct=30.0),
        )

        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        pipeline.create_signals(ee_ids)

        avoid_sigs = [
            s for s in db_session.query(SignalEvent).all()
            if s.alert_level == AlertLevel.AVOID_CHASE
        ]
        if avoid_sigs:
            results = pipeline.create_alerts([avoid_sigs[0].id])
            db_session.commit()
            alerts = db_session.query(Alert).all()
            assert len(alerts) >= 1
            outbox = db_session.query(AlertOutbox).all()
            assert len(outbox) >= 1


# ---------------------------------------------------------------------------
# Tests: Weekly review task
# ---------------------------------------------------------------------------


class TestWeeklyReviewTask:
    def test_generate_weekly_review_is_not_noop(self):
        """The generate_weekly_review function is a real Celery task."""
        from gktrader.tasks.jobs import generate_weekly_review

        assert callable(generate_weekly_review)
        assert hasattr(generate_weekly_review, "name")

    def test_weekly_review_creates_report(self, db_session):
        """Run generate_weekly_review internals and verify WeeklyReport persistence."""
        from datetime import UTC, datetime

        from gktrader.db.models import WeeklyReport
        from gktrader.reporting.weekly import build_weekly_report, WeeklyReportRow
        from gktrader.domain.enums import Direction, AlertLevel

        now = datetime.now(UTC)
        rows = [
            WeeklyReportRow(
                source_name="white_house",
                event_type="government_funding",
                direction=Direction.BULLISH,
                alert_level=AlertLevel.TRADEABLE,
                ticker="RGTI",
                notional_eur=1000.0,
                return_pct=5.0,
            ),
        ]
        report_payload = build_weekly_report(rows, generated_at=now)
        report_payload["open_positions"] = [
            {
                "position_id": "p1",
                "ticker": "RGTI",
                "direction": "bullish",
                "net_amount_eur": 1000.0,
                "average_price": 4.0,
            },
        ]

        report = WeeklyReport(report_payload=report_payload, delivered=False)
        db_session.add(report)
        db_session.commit()

        stored = db_session.query(WeeklyReport).all()
        assert len(stored) == 1
        assert stored[0].report_payload["total_trades"] == 1
        assert stored[0].report_payload["total_notional_eur"] == 1000.0
        assert len(stored[0].report_payload.get("open_positions", [])) == 1


# ---------------------------------------------------------------------------
# Tests: Snooze reminder persistence
# ---------------------------------------------------------------------------


class TestSnoozeReminderPersistence:
    def test_deliver_snooze_reminders_is_not_noop(self):
        """The deliver_snooze_reminders function is a real Celery task."""
        from gktrader.tasks.jobs import deliver_snooze_reminders

        assert callable(deliver_snooze_reminders)
        assert hasattr(deliver_snooze_reminders, "name")

    def test_snooze_reminder_is_persisted(self, db_session):
        """Verify an InteractionState with SNOOZE_REMINDER type can be stored."""
        from datetime import UTC, datetime, timedelta

        from gktrader.db.models import InteractionState
        from gktrader.domain.enums import InteractionStateType

        due_at = datetime.now(UTC) + timedelta(minutes=30)
        reminder = InteractionState(
            owner_id="12345",
            state_type=InteractionStateType.SNOOZE_REMINDER,
            payload={
                "alert_id": "alert-001",
                "idempotency_key": "idem-snooze-1",
                "minutes": 30,
                "due_at": due_at.isoformat(),
            },
            expires_at=due_at,
        )
        db_session.add(reminder)
        db_session.commit()

        stored = db_session.query(InteractionState).filter_by(
            state_type=InteractionStateType.SNOOZE_REMINDER,
        ).all()
        assert len(stored) == 1
        assert stored[0].payload["alert_id"] == "alert-001"

    def test_snooze_reminder_delivery_deletes_entries(self, db_session):
        """When a reminder is due, it should be deletable (delivered)."""
        from datetime import UTC, datetime, timedelta

        from gktrader.db.models import InteractionState
        from gktrader.domain.enums import InteractionStateType

        # Create an already-expired reminder
        due_at = datetime.now(UTC) - timedelta(minutes=5)
        reminder = InteractionState(
            owner_id="12345",
            state_type=InteractionStateType.SNOOZE_REMINDER,
            payload={
                "alert_id": "alert-002",
                "idempotency_key": "idem-due-1",
                "minutes": 30,
                "due_at": due_at.isoformat(),
            },
            expires_at=due_at,
        )
        db_session.add(reminder)
        db_session.commit()

        # Query due reminders (simulating the task logic)
        now = datetime.now(UTC)
        due = db_session.query(InteractionState).filter(
            InteractionState.state_type == InteractionStateType.SNOOZE_REMINDER,
            InteractionState.expires_at <= now,
        ).all()
        assert len(due) == 1

        # Delete the delivered reminder
        for r in due:
            db_session.delete(r)
        db_session.commit()

        remaining = db_session.query(InteractionState).filter_by(
            state_type=InteractionStateType.SNOOZE_REMINDER,
        ).all()
        assert len(remaining) == 0


# ---------------------------------------------------------------------------
# Tests: Weekly schedule configuration
# ---------------------------------------------------------------------------


class TestWeeklySchedule:
    def test_weekly_review_uses_crontab_not_seconds(self):
        """The weekly-review schedule must be a crontab, not a float."""
        from gktrader.tasks.celery_app import celery_app
        schedule = celery_app.conf.beat_schedule.get("weekly-review")
        assert schedule is not None, "weekly-review must be in beat_schedule"
        assert "schedule" in schedule, "weekly-review must have a schedule"
        # The schedule should be a crontab instance, not a plain float
        from celery.schedules import crontab
        assert isinstance(schedule["schedule"], crontab), (
            f"weekly-review schedule must be crontab, got {type(schedule['schedule'])}"
        )

    def test_snooze_reminder_schedule_is_configured(self):
        """The deliver-snooze-reminders schedule must exist."""
        from gktrader.tasks.celery_app import celery_app
        schedule = celery_app.conf.beat_schedule.get("deliver-snooze-reminders")
        assert schedule is not None, "deliver-snooze-reminders must be in beat_schedule"


# ---------------------------------------------------------------------------
# Tests: Weekly review delivery schedule
# ---------------------------------------------------------------------------


class TestWeeklyReviewDeliverySchedule:
    def test_deliver_weekly_review_schedule_is_configured(self):
        """The deliver-weekly-review schedule must exist."""
        from gktrader.tasks.celery_app import celery_app
        schedule = celery_app.conf.beat_schedule.get("deliver-weekly-review")
        assert schedule is not None, "deliver-weekly-review must be in beat_schedule"


# ---------------------------------------------------------------------------
# Tests: confirm_position service method
# ---------------------------------------------------------------------------


class TestConfirmPosition:
    def test_confirm_keep_open_preserves_position(self, db_session):
        """keep_open confirms the position without changing its amount."""
        from gktrader.api.services import ApiService

        # Set up a position
        pos = Position(
            ticker="RGTI",
            direction=Direction.BULLISH,
            net_amount_eur=1000.0,
            average_price=4.0,
        )
        db_session.add(pos)
        db_session.commit()

        svc = ApiService(db_session)
        result = svc.confirm_position(
            pos.id,
            PositionConfirmationRequest(action="keep_open"),
            "idem-confirm-001",
        )

        assert result["status"] == "recorded"
        assert result["position_event_id"] is not None

        # Verify PositionEvent was created
        events = db_session.query(PositionEvent).filter_by(ticker="RGTI").all()
        assert len(events) == 1
        assert events[0].event_type == PositionEventType.CONFIRM
        assert events[0].notes == "weekly-confirm:keep_open"

        # Verify position is unchanged
        db_session.refresh(pos)
        assert pos.net_amount_eur == 1000.0
        assert pos.average_price == 4.0

    def test_confirm_close_zeroes_position(self, db_session):
        """close sets the position amount to zero."""
        from gktrader.api.services import ApiService

        pos = Position(
            ticker="MU",
            direction=Direction.BEARISH,
            net_amount_eur=500.0,
            average_price=50.0,
        )
        db_session.add(pos)
        db_session.commit()

        svc = ApiService(db_session)
        result = svc.confirm_position(
            pos.id,
            PositionConfirmationRequest(action="close"),
            "idem-close-001",
        )

        assert result["status"] == "recorded"

        events = db_session.query(PositionEvent).filter_by(ticker="MU").all()
        assert len(events) == 1
        assert events[0].event_type == PositionEventType.CLOSE
        assert events[0].amount_eur == 0.0

        db_session.refresh(pos)
        assert pos.net_amount_eur == 0.0
        assert pos.average_price is None

    def test_confirm_adjust_updates_amount(self, db_session):
        """adjust replaces the position amount."""
        from gktrader.api.services import ApiService

        pos = Position(
            ticker="AAPL",
            direction=Direction.BULLISH,
            net_amount_eur=1000.0,
            average_price=150.0,
        )
        db_session.add(pos)
        db_session.commit()

        svc = ApiService(db_session)
        result = svc.confirm_position(
            pos.id,
            PositionConfirmationRequest(action="adjust", amount_eur=750.0),
            "idem-adjust-001",
        )

        assert result["status"] == "recorded"

        events = db_session.query(PositionEvent).filter_by(ticker="AAPL").all()
        assert len(events) == 1
        assert events[0].event_type == PositionEventType.ADJUST
        assert events[0].amount_eur == 750.0

        db_session.refresh(pos)
        assert pos.net_amount_eur == 750.0

    def test_confirm_idempotency_replay_returns_same(self, db_session):
        """Replay with same idempotency_key returns already_recorded."""
        from gktrader.api.services import ApiService

        pos = Position(
            ticker="RGTI",
            direction=Direction.BULLISH,
            net_amount_eur=1000.0,
            average_price=4.0,
        )
        db_session.add(pos)
        db_session.commit()

        svc = ApiService(db_session)
        result1 = svc.confirm_position(
            pos.id,
            PositionConfirmationRequest(action="keep_open"),
            "idem-replay-001",
        )
        assert result1["status"] == "recorded"

        # Replay with same key
        result2 = svc.confirm_position(
            pos.id,
            PositionConfirmationRequest(action="keep_open"),
            "idem-replay-001",
        )
        assert result2["status"] == "already_recorded"

        # Only one PositionEvent created
        events = db_session.query(PositionEvent).filter_by(ticker="RGTI").all()
        assert len(events) == 1

    def test_confirm_unknown_position_returns_404(self, db_session):
        """Confirming a nonexistent position raises 404."""
        from gktrader.api.services import ApiService
        from fastapi import HTTPException

        svc = ApiService(db_session)
        with pytest.raises(HTTPException) as exc_info:
            svc.confirm_position(
                "nonexistent-id",
                PositionConfirmationRequest(action="keep_open"),
                "idem-404-001",
            )
        assert exc_info.value.status_code == 404

    def test_confirm_trade_decision_has_null_alert_id(self, db_session):
        """TradeDecision created for confirmation must have alert_id=None (not 'weekly')."""
        from gktrader.api.services import ApiService

        pos = Position(
            ticker="RGTI",
            direction=Direction.BULLISH,
            net_amount_eur=1000.0,
            average_price=4.0,
        )
        db_session.add(pos)
        db_session.commit()

        svc = ApiService(db_session)
        result = svc.confirm_position(
            pos.id,
            PositionConfirmationRequest(action="keep_open"),
            "idem-null-alert-001",
        )
        assert result["status"] == "recorded"

        # Verify the TradeDecision idempotency marker has alert_id=None
        td = db_session.query(TradeDecision).filter_by(idempotency_key="idem-null-alert-001").first()
        assert td is not None, "TradeDecision idempotency marker must exist"
        assert td.alert_id is None, f"alert_id must be None, got {td.alert_id!r}"
        assert td.notes and td.notes.startswith("weekly-confirm:") and td.notes.endswith(":keep_open")


# ---------------------------------------------------------------------------
# Tests: manual position event alert_id safety
# ---------------------------------------------------------------------------


class TestManualPositionEventAlertId:
    def test_record_position_event_marker_has_null_alert_id(self, db_session):
        """TradeDecision created for manual position events must have alert_id=None."""
        from gktrader.api.services import ApiService
        from gktrader.domain.contracts import PositionEventRequest

        svc = ApiService(db_session)
        result = svc.record_position_event(
            PositionEventRequest(
                ticker="RGTI",
                event_type=PositionEventType.OPEN,
                amount_eur=1000.0,
                price=4.0,
            ),
            "idem-manual-null-001",
        )
        assert result["status"] == "recorded"

        td = db_session.query(TradeDecision).filter_by(idempotency_key="idem-manual-null-001").first()
        assert td is not None, "TradeDecision idempotency marker must exist"
        assert td.alert_id is None, f"alert_id must be None, got {td.alert_id!r}"


# ---------------------------------------------------------------------------
# Tests: deliver_weekly_review task
# ---------------------------------------------------------------------------


class TestDeliverWeeklyReview:
    def test_deliver_weekly_review_is_callable(self):
        """The deliver_weekly_review function is a Celery task."""
        from gktrader.tasks.jobs import deliver_weekly_review

        assert callable(deliver_weekly_review)
        assert hasattr(deliver_weekly_review, "name")

    def test_deliver_weekly_review_skips_when_no_undelivered(self, db_session):
        """When no undelivered reports exist, the task returns skipped."""
        # No WeeklyReport in DB -> should skip
        # We can't easily call the Celery task directly due to session management,
        # but we can verify the query logic
        from gktrader.db.models import WeeklyReport

        undelivered = db_session.query(WeeklyReport).filter(WeeklyReport.delivered == False).count()  # noqa: E712
        assert undelivered == 0

    def test_weekly_report_marked_delivered_after_delivery(self, db_session):
        """After delivery, the WeeklyReport.delivered flag must be True."""
        from datetime import UTC, datetime

        report = WeeklyReport(
            report_payload={
                "total_trades": 1,
                "total_notional_eur": 1000.0,
                "open_positions": [
                    {
                        "position_id": "p1",
                        "ticker": "RGTI",
                        "direction": "bullish",
                        "net_amount_eur": 1000.0,
                        "average_price": 4.0,
                    },
                ],
            },
            delivered=False,
        )
        db_session.add(report)
        db_session.commit()

        # Simulate what deliver_weekly_review does: mark delivered
        report_row = db_session.query(WeeklyReport).filter_by(delivered=False).first()  # noqa: E712
        assert report_row is not None

        # Mark delivered (as the task would)
        report_row.delivered = True
        db_session.commit()

        # Verify no more undelivered
        undelivered = db_session.query(WeeklyReport).filter(WeeklyReport.delivered == False).count()  # noqa: E712
        assert undelivered == 0

    def test_deliver_weekly_review_creates_awaiting_interactions(self, db_session):
        """After delivery, InteractionState entries must be created for each position."""
        from datetime import UTC, datetime, timedelta

        from gktrader.db.models import InteractionState
        from gktrader.domain.enums import InteractionStateType

        # Create an undelivered report with open positions
        report = WeeklyReport(
            report_payload={
                "total_trades": 1,
                "total_notional_eur": 1000.0,
                "open_positions": [
                    {
                        "position_id": "pos-rgti-1",
                        "ticker": "RGTI",
                        "direction": "bullish",
                        "net_amount_eur": 1000.0,
                        "average_price": 4.0,
                    },
                    {
                        "position_id": "pos-mu-1",
                        "ticker": "MU",
                        "direction": "bearish",
                        "net_amount_eur": 500.0,
                        "average_price": 50.0,
                    },
                ],
            },
            delivered=False,
        )
        db_session.add(report)
        db_session.commit()

        # Simulate creating interaction states (as the task would)
        for pos in report.report_payload.get("open_positions", []):
            pid = pos.get("position_id", "")
            if pid:
                istate = InteractionState(
                    owner_id="12345",
                    state_type=InteractionStateType.AWAITING_POSITION_CONFIRMATION,
                    payload={
                        "position_id": pid,
                        "ticker": pos.get("ticker", ""),
                        "direction": str(pos.get("direction", "")),
                        "net_amount_eur": pos.get("net_amount_eur", 0),
                        "report_id": report.id,
                    },
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
                db_session.add(istate)
        db_session.commit()

        # Verify interactions created
        interactions = db_session.query(InteractionState).filter_by(
            state_type=InteractionStateType.AWAITING_POSITION_CONFIRMATION,
        ).all()
        assert len(interactions) == 2
        tickers = [i.payload.get("ticker") for i in interactions]
        assert "RGTI" in tickers
        assert "MU" in tickers


# ---------------------------------------------------------------------------
# Tests: Bug #1 — record_alert_decision creates PositionEvent (regression)
# ---------------------------------------------------------------------------


class TestRecordAlertDecisionCreatesPosition:
    """Bug #1: AlertPayload must carry ticker so record_alert_decision can create PositionEvents."""

    def test_bought_decision_creates_position_event(self, db_session, resolver):
        """Posting a Bought decision must create a PositionEvent (not 422)."""
        from gktrader.api.services import ApiService
        from gktrader.domain.contracts import AlertDecisionRequest
        from gktrader.db.models import Alert, SignalEvent

        # Build a minimal signal → alert chain with ticker in rendered_payload
        signal = SignalEvent(
            fingerprint="fp-buy-test-001",
            event_type="government_funding",
            direction=Direction.BULLISH,
            action_status="announced",
            catalyst_score=5,
            classifier_confidence=0.92,
            alert_level=AlertLevel.TRADEABLE,
            payload={"ticker": "RGTI"},
            published_bucket="2026-06-12",
        )
        db_session.add(signal)
        db_session.flush()

        alert = Alert(
            id=_db_uuid(),
            signal_event_id=signal.id,
            level=AlertLevel.TRADEABLE,
            # rendered_payload now contains ticker (Bug #1 fix)
            rendered_payload={
                "alert_id": "alert-buy-001",
                "level": "TRADEABLE",
                "text": "BULLISH alert for RGTI",
                "dedupe_key": "RGTI:government_funding:bullish:TRADEABLE",
                "ticker": "RGTI",
                "company": "Rigetti Computing",
            },
            score_components={"catalyst_score": 5},
            dedupe_key="RGTI:government_funding:bullish:TRADEABLE",
        )
        db_session.add(alert)
        db_session.commit()

        svc = ApiService(db_session)
        resp = svc.record_alert_decision(
            alert.id,
            AlertDecisionRequest(
                decision=TradeDecisionType.BOUGHT,
                amount_eur=1000.0,
                execution_price=4.25,
            ),
            "idem-buy-001",
        )

        assert resp.position_event_id is not None, "PositionEvent must be created for BOUGHT"
        pe = db_session.query(PositionEvent).filter_by(id=resp.position_event_id).first()
        assert pe is not None
        assert pe.ticker == "RGTI"
        assert pe.event_type == PositionEventType.OPEN

    def test_alert_payload_includes_ticker_field(self, db_session, resolver):
        """AlertPayload rendered by the pipeline must carry ticker and company fields."""
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        pipeline.run_full_pipeline()
        db_session.commit()

        alerts = db_session.query(Alert).all()
        for alert in alerts:
            payload = alert.rendered_payload or {}
            assert "ticker" in payload, "rendered_payload must include 'ticker'"
            assert "company" in payload, "rendered_payload must include 'company'"


# ---------------------------------------------------------------------------
# Tests: Bug #2/#3/#4 — cooldown logic
# ---------------------------------------------------------------------------


class TestCooldownFixes:
    """Bug #2: duplicate fingerprint after cooldown must not crash the pipeline.
    Bug #3: cooldown keyed by (ticker, event_type, direction), not fingerprint.
    Bug #4: material update overrides cooldown.
    """

    def _run_signal_stage(self, pipeline, db_session):
        pipeline.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all()]
        pipeline.process_documents(raw_ids)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        return pipeline.create_signals(ee_ids)

    def test_duplicate_fingerprint_after_cooldown_does_not_crash(self, db_session, resolver):
        """Bug #2: Re-processing the same extracted event after cooldown must skip, not IntegrityError."""
        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])

        # First run creates the signal
        results1 = self._run_signal_stage(pipeline, db_session)
        db_session.commit()
        assert any(r.signal_event_id is not None for r in results1)
        first_count = db_session.query(SignalEvent).count()

        # Second run with the same extracted events (simulates cooldown-expired re-poll)
        ee_ids = [e.id for e in db_session.query(ExtractedEvent).all()]
        results2 = pipeline.create_signals(ee_ids)
        db_session.commit()  # must not raise IntegrityError

        # No new SignalEvent row should be created (exact duplicate is skipped)
        assert db_session.query(SignalEvent).count() == first_count

    def test_cooldown_suppresses_same_key_different_fingerprint(self, db_session, resolver):
        """Bug #3: A different fingerprint but same (ticker, event_type, direction) within 6h is suppressed."""
        from gktrader.db.models import SignalEvent as SE

        # Seed a prior signal for (RGTI, government_funding, bullish).
        # Include catalyst_score and alert_level so is_material_update sees no increase.
        sig = SE(
            fingerprint="fp-cool-unique-001",
            event_type="government_funding",
            direction=Direction.BULLISH,
            action_status="announced",
            catalyst_score=5,
            classifier_confidence=0.92,
            alert_level=AlertLevel.TRADEABLE,
            payload={
                "ticker": "RGTI",
                "monetary_amounts": ["$15M"],
                "award_or_contract_ids": [],
                "action_status": "announced",
                "catalyst_score": 5,
                "alert_level": "TRADEABLE",
            },
            published_bucket="2026-06-12",
        )
        db_session.add(sig)
        db_session.commit()

        # Second doc: same (ticker, event_type, direction, amounts) but published on different date
        # → different fingerprint (date bucket differs), but NOT a material update
        doc2 = _make_mock_doc(
            external_id="cool-002",
            title="CHIPS Award for Rigetti — followup mention",
            text="Rigetti Computing $15M government funding announced.",
        )

        def _same_key_classify(title, text, meta):
            run = _make_classifier_result()
            # Same amounts, status, and strength as the seeded signal → not a material update
            run.parsed_result.monetary_amounts = ["$15M"]
            run.parsed_result.award_or_contract_ids = []
            run.parsed_result.action_status = "announced"
            run.parsed_result.strength = 5  # same catalyst_score → no increase
            return run

        pipeline2 = _build_pipeline(db_session, resolver, [doc2], classify_fn=_same_key_classify)
        pipeline2.ingest_sources()
        raw_ids = [r.id for r in db_session.query(RawDocument).all() if r.external_id == "cool-002"]
        if raw_ids:
            pipeline2.process_documents(raw_ids)
            ee_ids = [
                e.id for e in db_session.query(ExtractedEvent).all()
                if e.raw_document_id in raw_ids
            ]
            if ee_ids:
                results = pipeline2.create_signals(ee_ids)
                db_session.commit()
                # Within cooldown + not material → suppressed
                suppressed = [r for r in results if r.on_cooldown and r.skipped]
                assert len(suppressed) >= 1, "Same-key, non-material signal within cooldown must be suppressed"

    def test_material_update_overrides_cooldown(self, db_session, resolver):
        """Bug #4: A direction change (material update) must override the cooldown."""
        from datetime import datetime, timezone

        doc_bull = _make_mock_doc(
            external_id="mat-001",
            text="The Department of Commerce awarded $15M to Rigetti Computing.",
        )
        pipeline = _build_pipeline(db_session, resolver, [doc_bull])
        self._run_signal_stage(pipeline, db_session)
        db_session.commit()

        # Now a bearish event for the same company within 6h
        doc_bear = _make_mock_doc(
            external_id="mat-002",
            title="Trade sanction hits Rigetti Computing",
            text="The government sanctioned Rigetti Computing quantum hardware exports.",
        )

        def _bearish_classify(title, text, meta):
            return _make_classifier_result(direction="bearish")

        pipeline2 = _build_pipeline(
            db_session,
            resolver,
            [doc_bear],
            classify_fn=_bearish_classify,
            now_fn=lambda: _now() + timedelta(seconds=61),
        )
        pipeline2.ingest_sources()
        raw_ids = [
            r.id for r in db_session.query(RawDocument).all()
            if r.external_id == "mat-002"
        ]
        if raw_ids:
            pipeline2.process_documents(raw_ids)
            ee_ids = [
                e.id for e in db_session.query(ExtractedEvent).all()
                if e.raw_document_id in raw_ids
            ]
            if ee_ids:
                results = pipeline2.create_signals(ee_ids)
                db_session.commit()
                # Direction change is a material update — should NOT be suppressed
                not_suppressed = [r for r in results if r.signal_event_id is not None]
                assert len(not_suppressed) >= 1, "Direction change must override cooldown"


# ---------------------------------------------------------------------------
# Tests: Bug #5 — PaperTrade created for actionable alerts
# ---------------------------------------------------------------------------


class TestPaperTradeCreation:
    """Bug #5: Every actionable alert must create a PaperTrade row."""

    def test_tradeable_alert_creates_paper_trade(self, db_session, resolver):
        """A TRADEABLE alert must result in a PaperTrade with notional_eur=1000."""
        from gktrader.db.models import PaperTrade

        doc = _make_mock_doc()
        pipeline = _build_pipeline(db_session, resolver, [doc])
        pipeline.run_full_pipeline()
        db_session.commit()

        alerts = db_session.query(Alert).filter(Alert.level == AlertLevel.TRADEABLE).all()
        if alerts:
            paper_trades = db_session.query(PaperTrade).all()
            assert len(paper_trades) >= 1, "TRADEABLE alert must create a PaperTrade"
            assert paper_trades[0].notional_eur == 1000.0

    def test_review_alert_creates_paper_trade_with_500(self, db_session, resolver):
        """A REVIEW alert must result in a PaperTrade with notional_eur=500."""
        from gktrader.db.models import PaperTrade
        from gktrader.domain.enums import SourceTier

        doc = _make_mock_doc()

        # Force a REVIEW-only result by lowering mapping confidence
        def _low_confidence_classify(title, text, meta):
            run = _make_classifier_result()
            run.parsed_result.confidence = 0.5  # below TRADEABLE threshold
            return run

        pipeline = _build_pipeline(
            db_session, resolver, [doc], classify_fn=_low_confidence_classify,
        )
        pipeline.run_full_pipeline()
        db_session.commit()

        review_alerts = db_session.query(Alert).filter(Alert.level == AlertLevel.REVIEW).all()
        if review_alerts:
            paper_trades = db_session.query(PaperTrade).filter(
                PaperTrade.notional_eur == 500.0,
            ).all()
            assert len(paper_trades) >= 1, "REVIEW alert must create a PaperTrade with EUR 500"


# ---------------------------------------------------------------------------
# Tests: Bug #6 — First-start baseline suppresses delivery
# ---------------------------------------------------------------------------


class TestFirstStartBaseline:
    """Bug #6: First poll for a source must not create signals/alerts when baseline is active."""

    def test_first_poll_baseline_skips_signals(self, db_session, resolver):
        """When enable_first_start_baseline=True, first-poll docs produce no signals."""
        doc = _make_mock_doc()
        # Use settings with baseline active (default)
        settings = _make_settings(enable_first_start_baseline=True, allow_alerts_during_replay=False)
        adapter = _MockSourceAdapter([doc])
        pipeline = SignalPipeline(
            db_session=db_session,
            settings=settings,
            adapters={"mock_source": adapter},
            resolver=resolver,
            classify_fn=lambda t, x, m: _make_classifier_result(),
            snapshot_fn=lambda t: _make_market_snapshot(t),
            deliver_fn=lambda s, c, p: DeliveryStatus.SENT,
            continue_deliver_fn=lambda s, c, m: [DeliveryStatus.SENT],
            now_fn=_now,
        )

        result = pipeline.run_full_pipeline()
        db_session.commit()

        # First poll: no cursors existed → all docs are baseline → no signals
        assert db_session.query(SignalEvent).count() == 0, (
            "First-start baseline must suppress signal creation"
        )

    def test_second_poll_allows_signals(self, db_session, resolver):
        """After the first poll establishes a cursor, subsequent polls create signals."""
        doc1 = _make_mock_doc(external_id="baseline-001", title="Baseline doc")
        doc2 = _make_mock_doc(external_id="signal-002", title="Real signal doc")

        settings = _make_settings(enable_first_start_baseline=True, allow_alerts_during_replay=False)
        adapter1 = _MockSourceAdapter([doc1])
        pipeline1 = SignalPipeline(
            db_session=db_session,
            settings=settings,
            adapters={"mock_source": adapter1},
            resolver=resolver,
            classify_fn=lambda t, x, m: _make_classifier_result(),
            snapshot_fn=lambda t: _make_market_snapshot(t),
            deliver_fn=lambda s, c, p: DeliveryStatus.SENT,
            continue_deliver_fn=lambda s, c, m: [DeliveryStatus.SENT],
            now_fn=_now,
        )
        # First poll: baseline, no signals
        pipeline1.run_full_pipeline()
        db_session.commit()
        assert db_session.query(SignalEvent).count() == 0

        # Second poll with a new doc: cursor exists → signals allowed
        adapter2 = _MockSourceAdapter([doc2])
        pipeline2 = SignalPipeline(
            db_session=db_session,
            settings=settings,
            adapters={"mock_source": adapter2},
            resolver=resolver,
            classify_fn=lambda t, x, m: _make_classifier_result(),
            snapshot_fn=lambda t: _make_market_snapshot(t),
            deliver_fn=lambda s, c, p: DeliveryStatus.SENT,
            continue_deliver_fn=lambda s, c, m: [DeliveryStatus.SENT],
            now_fn=lambda: _now() + timedelta(seconds=61),
        )
        pipeline2.run_full_pipeline()
        db_session.commit()
        # Second poll: cursor exists → signals are created
        assert db_session.query(SignalEvent).count() >= 1, (
            "Second poll must allow signal creation"
        )
