from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gktrader.db.base import Base
from gktrader.db.models import (
    Alert,
    EventCompany,
    EventEvidence,
    ExtractedEvent,
    ProcessingRun,
    RawDocument,
    SignalEvent,
)
from gktrader.domain.enums import AlertLevel, Direction, ProcessingStatus, SourceTier
from gktrader.ui.service import UIService


def _id() -> str:
    return str(uuid.uuid4())


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 13, hour, 0, 0, tzinfo=timezone.utc)


def test_recent_news_returns_classification_and_rating_fields() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        raw = RawDocument(
            id=_id(),
            correlation_id="corr-1",
            source_name="whitehouse",
            source_tier=SourceTier.TIER_1,
            fetch_path="rss",
            external_id="wh-1",
            canonical_url="https://example.com/news/1",
            title="Commerce awards funding to Rigetti",
            text="Sample body",
            content_hash="hash-1",
            published_at=_dt(10),
            detected_at=_dt(11),
            source_metadata={},
        )
        db.add(raw)

        proc = ProcessingRun(
            id=_id(),
            raw_document_id=raw.id,
            classifier_model="test-model",
            prompt_version="v1",
            prompt_hash="hash",
            parsed_result={
                "relevant": True,
                "event_type": "government_funding",
                "direction": "bullish",
                "confidence": 0.92,
                "companies": [{"name": "Rigetti Computing"}],
            },
            status=ProcessingStatus.SUCCEEDED,
        )
        db.add(proc)

        extracted = ExtractedEvent(
            id=_id(),
            raw_document_id=raw.id,
            processing_run_id=proc.id,
            event_payload={"event_type": "government_funding"},
        )
        db.add(extracted)

        db.add(
            EventCompany(
                id=_id(),
                extracted_event_id=extracted.id,
                company_id=None,
                candidate_name="Rigetti Computing",
                mapping_confidence=0.88,
                mapping_status="resolved",
            )
        )

        db.add(
            SignalEvent(
                id=_id(),
                fingerprint="fp-1",
                event_type="government_funding",
                direction=Direction.BULLISH,
                action_status="announced",
                catalyst_score=4,
                classifier_confidence=0.92,
                alert_level=AlertLevel.TRADEABLE,
                primary_company_id=None,
                payload={
                    "ticker": "RGTI",
                    "companies": ["Rigetti Computing"],
                    "extracted_event_ids": [extracted.id],
                },
                published_bucket="2026-06-13",
            )
        )
        db.commit()

        rows = UIService(db).recent_news(limit=5)

    assert len(rows) == 1
    row = rows[0]
    assert row["source_name"] == "whitehouse"
    assert row["company"] == "Rigetti Computing"
    assert row["processing_status"] == "succeeded"
    assert row["event_type"] == "government_funding"
    assert row["ticker"] == "RGTI"
    assert row["alert_level"] == "TRADEABLE"
    assert row["catalyst_score"] == 4
    assert row["mapping_confidence"] == 0.88


def test_recent_news_returns_latest_version_per_external_id() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        older = RawDocument(
            id=_id(),
            correlation_id="corr-old",
            source_name="truthsocial",
            source_tier=SourceTier.TIER_1,
            fetch_path="playwright",
            external_id="ts-pw-1",
            canonical_url="https://example.com/post/1",
            title="Pinned Truth Donald J. Trump @realDonaldTrump · 16h ...",
            text="older",
            content_hash="hash-old",
            published_at=_dt(9),
            detected_at=_dt(10),
            source_metadata={},
        )
        newer = RawDocument(
            id=_id(),
            correlation_id="corr-new",
            source_name="truthsocial",
            source_tier=SourceTier.TIER_1,
            fetch_path="playwright",
            external_id="ts-pw-1",
            canonical_url="https://example.com/post/1",
            title="Barack Hussein Obama’s Deal with Iran",
            text="newer",
            content_hash="hash-new",
            published_at=_dt(9),
            detected_at=_dt(11),
            source_metadata={},
        )
        db.add_all([older, newer])
        db.commit()

        rows = UIService(db).recent_news(limit=5)

    assert len(rows) == 1
    assert rows[0]["id"] == newer.id
    assert rows[0]["title"] == "Barack Hussein Obama’s Deal with Iran"


def test_get_news_detail_returns_linked_signal_and_alert() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        raw = RawDocument(
            id=_id(),
            correlation_id="corr-2",
            source_name="whitehouse",
            source_tier=SourceTier.TIER_1,
            fetch_path="rss",
            external_id="wh-2",
            canonical_url="https://example.com/news/2",
            title="Rigetti receives award",
            text="Award text body",
            content_hash="hash-2",
            published_at=_dt(12),
            detected_at=_dt(13),
            source_metadata={"region": "us"},
        )
        db.add(raw)

        proc = ProcessingRun(
            id=_id(),
            raw_document_id=raw.id,
            classifier_model="test-model",
            prompt_version="v1",
            prompt_hash="hash",
            parsed_result={
                "relevant": True,
                "event_type": "government_funding",
                "direction": "bullish",
                "confidence": 0.93,
                "companies": [{"name": "Rigetti Computing"}],
            },
            status=ProcessingStatus.SUCCEEDED,
        )
        db.add(proc)

        extracted = ExtractedEvent(
            id=_id(),
            raw_document_id=raw.id,
            processing_run_id=proc.id,
            event_payload={"event_type": "government_funding"},
        )
        db.add(extracted)

        db.add(
            EventCompany(
                id=_id(),
                extracted_event_id=extracted.id,
                company_id=None,
                candidate_name="Rigetti Computing",
                mapping_confidence=0.91,
                mapping_status="resolved",
            )
        )

        signal = SignalEvent(
            id=_id(),
            fingerprint="fp-2",
            event_type="government_funding",
            direction=Direction.BULLISH,
            action_status="announced",
            catalyst_score=5,
            classifier_confidence=0.93,
            alert_level=AlertLevel.TRADEABLE,
            primary_company_id=None,
            payload={
                "ticker": "RGTI",
                "companies": ["Rigetti Computing"],
                "rationale": "Funding has direct commercial impact.",
                "extracted_event_ids": [extracted.id],
            },
            published_bucket="2026-06-13",
        )
        db.add(signal)
        db.flush()

        db.add(
            EventEvidence(
                id=_id(),
                signal_event_id=signal.id,
                raw_document_id=raw.id,
                evidence_text="Rigetti receives strategic funding.",
                start_offset=0,
                end_offset=35,
            )
        )

        alert = Alert(
            id=_id(),
            signal_event_id=signal.id,
            market_snapshot_id=None,
            level=AlertLevel.TRADEABLE,
            rendered_payload={"ticker": "RGTI", "company": "Rigetti Computing"},
            score_components={"catalyst_score": 5},
            dedupe_key="dedupe-2",
        )
        db.add(alert)
        db.commit()

        detail = UIService(db).get_news_detail(raw.id)

    assert detail is not None
    assert detail["id"] == raw.id
    assert detail["signal_id"] == signal.id
    assert detail["alert_id"] == alert.id
    assert detail["ticker"] == "RGTI"
    assert detail["company"] == "Rigetti Computing"
    assert detail["text"] == "Award text body"
    assert detail["rationale"] == "Funding has direct commercial impact."
    assert detail["source_url"] == "https://example.com/news/2"
    assert detail["source_metadata"] == {"region": "us"}
    assert detail["evidence"][0]["text"] == "Rigetti receives strategic funding."


def test_get_news_detail_prefers_full_text_from_metadata() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        raw = RawDocument(
            id=_id(),
            correlation_id="corr-3",
            source_name="truthsocial",
            source_tier=SourceTier.TIER_1,
            fetch_path="index_fallback",
            external_id="ts-pw-2",
            canonical_url="https://truthsocial.com/",
            title="Congratulations to Jim Dolan and the New York Knicks!!! What a year it has been…",
            text="Congratulations to Jim Dolan and the New York Knicks!!! What a year it has been…",
            content_hash="hash-3",
            published_at=None,
            detected_at=_dt(14),
            source_metadata={
                "playwright_line": 2,
                "normalized_line": (
                    "Congratulations to Jim Dolan and the New York Knicks!!! What a year it has "
                    "been and there is much more to come in the future."
                ),
            },
        )
        db.add(raw)
        db.commit()

        detail = UIService(db).get_news_detail(raw.id)

    assert detail is not None
    assert detail["text"].endswith("there is much more to come in the future.")
    assert detail["text_truncated"] is False


def test_get_alert_detail_chart_includes_all_stock_events_for_selected_range(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    bars = [
        {
            "timestamp": datetime(2026, 5, 30, 0, 0, tzinfo=timezone.utc),
            "open": 11.0,
            "high": 11.3,
            "low": 10.8,
            "close": 11.2,
            "volume": 1000,
        },
        {
            "timestamp": datetime(2026, 6, 5, 0, 0, tzinfo=timezone.utc),
            "open": 11.2,
            "high": 11.7,
            "low": 11.1,
            "close": 11.5,
            "volume": 1200,
        },
        {
            "timestamp": datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc),
            "open": 11.5,
            "high": 12.0,
            "low": 11.4,
            "close": 11.9,
            "volume": 1400,
        },
        {
            "timestamp": datetime(2026, 6, 13, 0, 0, tzinfo=timezone.utc),
            "open": 11.9,
            "high": 12.4,
            "low": 11.8,
            "close": 12.2,
            "volume": 1800,
        },
    ]

    def fake_fetch_chart_bars(self, ticker: str, *, start, end, timeframe):  # noqa: ANN001
        assert ticker == "RGTI"
        assert timeframe == "1Hour"
        return bars

    monkeypatch.setattr(UIService, "_fetch_chart_bars", fake_fetch_chart_bars)

    with Session(engine) as db:
        older_doc = RawDocument(
            id=_id(),
            correlation_id="corr-oldest",
            source_name="whitehouse",
            source_tier=SourceTier.TIER_1,
            fetch_path="rss",
            external_id="wh-oldest",
            canonical_url="https://example.com/news/oldest",
            title="Oldest catalyst",
            text="Oldest body",
            content_hash="hash-oldest",
            published_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc),
            source_metadata={},
        )
        older_extracted = ExtractedEvent(
            id=_id(),
            raw_document_id=older_doc.id,
            processing_run_id=_id(),
            event_payload={"event_type": "government_funding"},
        )
        older_signal = SignalEvent(
            id=_id(),
            fingerprint="fp-oldest",
            event_type="government_funding",
            direction=Direction.BULLISH,
            action_status="announced",
            catalyst_score=3,
            classifier_confidence=0.81,
            alert_level=AlertLevel.REVIEW,
            primary_company_id=None,
            payload={
                "ticker": "RGTI",
                "companies": ["Rigetti Computing"],
                "rationale": "Earlier policy support.",
                "extracted_event_ids": [older_extracted.id],
            },
            published_bucket="2026-06-01",
            created_at=datetime(2026, 6, 1, 12, 5, tzinfo=timezone.utc),
        )

        middle_doc = RawDocument(
            id=_id(),
            correlation_id="corr-middle",
            source_name="whitehouse",
            source_tier=SourceTier.TIER_1,
            fetch_path="rss",
            external_id="wh-middle",
            canonical_url="https://example.com/news/middle",
            title="Middle catalyst",
            text="Middle body",
            content_hash="hash-middle",
            published_at=datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 6, 8, 10, 5, tzinfo=timezone.utc),
            source_metadata={},
        )
        middle_extracted = ExtractedEvent(
            id=_id(),
            raw_document_id=middle_doc.id,
            processing_run_id=_id(),
            event_payload={"event_type": "government_funding"},
        )
        middle_signal = SignalEvent(
            id=_id(),
            fingerprint="fp-middle",
            event_type="government_funding",
            direction=Direction.BULLISH,
            action_status="announced",
            catalyst_score=4,
            classifier_confidence=0.88,
            alert_level=AlertLevel.TRADEABLE,
            primary_company_id=None,
            payload={
                "ticker": "RGTI",
                "companies": ["Rigetti Computing"],
                "rationale": "Second catalyst.",
                "extracted_event_ids": [middle_extracted.id],
            },
            published_bucket="2026-06-08",
            created_at=datetime(2026, 6, 8, 10, 5, tzinfo=timezone.utc),
        )

        current_doc = RawDocument(
            id=_id(),
            correlation_id="corr-current",
            source_name="whitehouse",
            source_tier=SourceTier.TIER_1,
            fetch_path="rss",
            external_id="wh-current",
            canonical_url="https://example.com/news/current",
            title="Current catalyst",
            text="Current body",
            content_hash="hash-current",
            published_at=datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc),
            detected_at=datetime(2026, 6, 13, 9, 5, tzinfo=timezone.utc),
            source_metadata={},
        )
        current_extracted = ExtractedEvent(
            id=_id(),
            raw_document_id=current_doc.id,
            processing_run_id=_id(),
            event_payload={"event_type": "government_funding"},
        )
        current_signal = SignalEvent(
            id=_id(),
            fingerprint="fp-current",
            event_type="government_funding",
            direction=Direction.BULLISH,
            action_status="announced",
            catalyst_score=5,
            classifier_confidence=0.94,
            alert_level=AlertLevel.TRADEABLE,
            primary_company_id=None,
            payload={
                "ticker": "RGTI",
                "companies": ["Rigetti Computing"],
                "rationale": "Current catalyst.",
                "extracted_event_ids": [current_extracted.id],
            },
            published_bucket="2026-06-13",
            created_at=datetime(2026, 6, 13, 9, 5, tzinfo=timezone.utc),
        )

        current_alert = Alert(
            id=_id(),
            signal_event_id=current_signal.id,
            market_snapshot_id=None,
            level=AlertLevel.TRADEABLE,
            rendered_payload={"ticker": "RGTI", "company": "Rigetti Computing"},
            score_components={"catalyst_score": 5},
            dedupe_key="alert-current",
            created_at=datetime(2026, 6, 13, 9, 6, tzinfo=timezone.utc),
        )

        db.add_all([
            older_doc,
            older_extracted,
            older_signal,
            middle_doc,
            middle_extracted,
            middle_signal,
            current_doc,
            current_extracted,
            current_signal,
            current_alert,
        ])
        db.commit()

        detail = UIService(db).get_alert_detail(current_alert.id, range_key="1M")

    assert detail is not None
    chart = detail["chart"]
    assert chart["available"] is True
    assert chart["selected_range"] == "1M"
    assert chart["visible_event_count"] == 2
    assert chart["total_event_count"] == 3
    assert all(not event["is_focus"] for event in chart["event_lines"])
    assert any(event["news_id"] == older_doc.id for event in chart["event_lines"])
    assert chart["points_attr"]


def test_get_news_detail_flags_unrecoverable_truncated_truthsocial_text() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        raw = RawDocument(
            id=_id(),
            correlation_id="corr-4",
            source_name="truthsocial",
            source_tier=SourceTier.TIER_1,
            fetch_path="index_fallback",
            external_id="ts-pw-3",
            canonical_url="https://truthsocial.com/",
            title="Truncated post…",
            text="Truncated post…",
            content_hash="hash-4",
            published_at=None,
            detected_at=_dt(15),
            source_metadata={"playwright_line": 1},
        )
        db.add(raw)
        db.commit()

        detail = UIService(db).get_news_detail(raw.id)

    assert detail is not None
    assert detail["text"] == "Truncated post…"
    assert detail["text_truncated"] is True
