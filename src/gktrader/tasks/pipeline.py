"""Signal pipeline: orchestrates source ingestion through alert outbox creation.

This module provides the core SignalPipeline class that connects all existing
adapters, classifiers, resolvers, scorers, market-data providers, renderers,
and outbox components into a single persisted end-to-end pipeline.

All external calls (HTTP, LLM) are injected via callables so that the pipeline
is fully testable with mocks.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from gktrader.alerts.outbox import (
    OutboxEntry,
    claim_outbox,
    generate_idempotency_key,
    mark_delivery_outcome,
)
from gktrader.alerts.renderer import AlertRenderContext, render_alert_payload
from gktrader.alerts.sender import DeliveryStatus, send_alert, send_continuation_messages
from gktrader.config.settings import Settings
from gktrader.db.models import (
    Alert,
    AlertDelivery,
    AlertOutbox,
    EventCompany,
    EventEvidence,
    ExtractedEvent,
    MarketSnapshot,
    PaperTrade,
    ProcessingRun,
    RawDocument,
    SignalEvent,
    SourceCursor,
    SourceDefinition,
    SourcePollRun,
)
from gktrader.domain.contracts import (
    MarketSnapshotContract,
    NormalizedDocument,
    PriorBullishSignal,
    SignalDecision,
    TickerCandidate,
)
from gktrader.domain.enums import (
    AlertLevel,
    DeliveryStatus as DeliveryStatusEnum,
    Direction,
    PollRunStatus,
    ProcessingStatus,
    SourceTier,
)
from gktrader.intelligence.classifier import ClassificationRun
from gktrader.intelligence.cooldown import CooldownKey, CooldownState, is_material_update
from gktrader.intelligence.fingerprint import compute_event_fingerprint
from gktrader.intelligence.resolver import TickerResolver
from gktrader.intelligence.scoring import ScoreContext, compute_actionability
from gktrader.marketdata.downgrade import apply_market_downgrade
from gktrader.reporting.paper import make_paper_entry
from gktrader.sources.base import SourceAdapter
from gktrader.sources.truthsocial import (
    TruthSocialAdapter,
    _normalize_playwright_line,
    _truncate_title,
    resolve_truthsocial_source_url,
)
from gktrader.intelligence.prompts import compute_prompt_hash, get_prompt_info


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _resolve_alert_source_url(raw_doc: RawDocument | None) -> str:
    if raw_doc is None:
        return ""

    canonical_url = raw_doc.canonical_url or ""
    if raw_doc.source_name == "truthsocial":
        return resolve_truthsocial_source_url(canonical_url, raw_doc.source_metadata)
    return canonical_url


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Pipeline result types
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    """Result of one source ingestion cycle."""

    source_name: str
    status: PollRunStatus = PollRunStatus.STARTED
    items_fetched: int = 0
    new_documents: int = 0
    poll_run_id: str | None = None
    raw_document_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    is_first_poll: bool = False
    skipped: bool = False


@dataclass
class ProcessingResult:
    """Result of processing one raw document through classification."""

    raw_document_id: str
    status: ProcessingStatus = ProcessingStatus.PENDING
    processing_run_id: str | None = None
    extracted_event_id: str | None = None
    event_company_ids: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SignalResult:
    """Result of signal event creation / dedupe."""

    extracted_event_id: str
    signal_event_id: str | None = None
    alert_level: AlertLevel = AlertLevel.IGNORE
    catalyst_score: int = 0
    direction: Direction = Direction.NEUTRAL
    event_type: str = ""
    is_duplicate: bool = False
    on_cooldown: bool = False
    skipped: bool = False
    reason: str = ""


@dataclass
class AlertResult:
    """Result of alert creation and outbox enqueue."""

    signal_event_id: str
    alert_id: str | None = None
    alert_level: AlertLevel = AlertLevel.IGNORE
    outbox_id: str | None = None
    delivery_ready: bool = False
    skipped: bool = False
    reason: str = ""


@dataclass
class DeliveryAttemptResult:
    """Result of one outbox delivery attempt."""

    outbox_id: str
    alert_id: str
    status: DeliveryStatusEnum = DeliveryStatusEnum.PENDING
    message_id: str | None = None
    error: str | None = None


@dataclass
class PipelineResult:
    """Aggregate result of a full pipeline run."""

    ingest_results: list[IngestResult] = field(default_factory=list)
    processing_results: list[ProcessingResult] = field(default_factory=list)
    signal_results: list[SignalResult] = field(default_factory=list)
    alert_results: list[AlertResult] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def total_raw_documents(self) -> int:
        return sum(r.new_documents for r in self.ingest_results)

    @property
    def total_signals(self) -> int:
        return sum(1 for r in self.signal_results if r.signal_event_id)

    @property
    def total_alerts(self) -> int:
        return sum(1 for r in self.alert_results if r.alert_id)


# ---------------------------------------------------------------------------
# Classify helper
# ---------------------------------------------------------------------------


def _classify_sync(
    title: str, text: str, source_metadata: dict[str, Any] | None = None,
) -> ClassificationRun:
    """Synchronous wrapper for async classifier, used in Celery tasks."""
    from gktrader.config.settings import get_settings
    from gktrader.intelligence.classifier import ClassifierConfig, OpenRouterClassifier

    settings = get_settings()
    if not settings.openrouter_api_key:
        run = ClassificationRun(
            model=settings.openrouter_model,
            prompt_version=get_prompt_info().version,
            prompt_hash=get_prompt_info().hash,
            status=ProcessingStatus.FAILED,
            error="OpenRouter API key not configured",
        )
        return run

    config = ClassifierConfig(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        fallback_model=settings.openrouter_fallback_model,
    )
    classifier = OpenRouterClassifier(config)

    async def _run():
        try:
            result = await classifier.classify(title, text, source_metadata)
            return result
        finally:
            await classifier.close()

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class SignalPipeline:
    """Orchestrates the full signal pipeline end-to-end.

    Connects source adapters, classifier, resolver, scoring, market snapshot,
    renderer, and outbox components.  Every stage records status, timestamps,
    and correlation IDs.

    External calls are injected via callables so the pipeline is testable
    with mocks.
    """

    def __init__(
        self,
        db_session: Any,  # Session
        settings: Settings,
        adapters: dict[str, SourceAdapter],  # source_name -> adapter
        resolver: TickerResolver,
        *,
        classify_fn: Callable[[str, str, dict[str, Any] | None], ClassificationRun]
        | None = None,
        snapshot_fn: Callable[[str], MarketSnapshotContract] | None = None,
        deliver_fn: (
            Callable[[Settings, int, Any], DeliveryStatus] | None
        ) = None,
        continue_deliver_fn: (
            Callable[[Settings, int, list[str]], list[DeliveryStatus]] | None
        ) = None,
        now_fn: Callable[[], datetime] = _now,
    ):
        self.db = db_session
        self.settings = settings
        self.adapters = adapters
        self.resolver = resolver
        self._classify = classify_fn or _classify_sync
        self._snapshot = snapshot_fn or self._default_snapshot
        self._deliver = deliver_fn or send_alert
        self._continue_deliver = continue_deliver_fn or send_continuation_messages
        self._now = now_fn

    def _repair_truthsocial_index_fallback_document(
        self,
        existing: RawDocument,
        normalized_text: str,
        source_metadata: dict[str, Any],
    ) -> bool:
        if existing.fetch_path != "index_fallback":
            return False
        if len(normalized_text) <= len(existing.text or "") and not existing.text.endswith("…"):
            return False

        new_content_hash = _hash_text(normalized_text)
        duplicate = (
            self.db.query(RawDocument)
            .filter_by(
                source_name=existing.source_name,
                external_id=existing.external_id,
                content_hash=new_content_hash,
            )
            .first()
        )
        if duplicate is not None and duplicate.id != existing.id:
            return False

        metadata = dict(existing.source_metadata or {})
        metadata.update({
            "normalized_line": normalized_text,
            "backfilled_from": source_metadata.get("source", source_metadata.get("backfilled_from", "truthsocial")),
        })
        if source_metadata.get("original_id"):
            metadata["original_id"] = source_metadata["original_id"]
        if source_metadata.get("mirror_timestamp"):
            metadata["mirror_timestamp"] = source_metadata["mirror_timestamp"]

        existing.title = _truncate_title(normalized_text)
        existing.text = normalized_text
        existing.content_hash = new_content_hash
        existing.source_metadata = metadata
        self.db.flush()
        return True

    def _repair_truthsocial_truncated_documents(
        self,
        adapter: SourceAdapter,
        items: list[Any],
    ) -> None:
        if not isinstance(adapter, TruthSocialAdapter):
            return

        for item in items:
            try:
                raw = adapter.fetch_detail(item)
                doc = adapter.normalize(raw)
            except Exception:
                continue

            normalized_text = _normalize_playwright_line(doc.text)
            if not normalized_text:
                continue

            playwright_external_id = (
                "ts-pw-" + hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()[:16]
            )
            existing_docs = (
                self.db.query(RawDocument)
                .filter_by(
                    source_name="truthsocial",
                    external_id=playwright_external_id,
                    fetch_path="index_fallback",
                )
                .all()
            )
            if not existing_docs:
                candidates = (
                    self.db.query(RawDocument)
                    .filter(
                        RawDocument.source_name == "truthsocial",
                        RawDocument.fetch_path == "index_fallback",
                        RawDocument.text.like("%…"),
                    )
                    .all()
                )
                existing_docs = [
                    existing
                    for existing in candidates
                    if normalized_text.startswith(
                        _normalize_playwright_line((existing.text or "").removesuffix("…"))
                    )
                ]
            for existing in existing_docs:
                self._repair_truthsocial_index_fallback_document(
                    existing,
                    normalized_text,
                    doc.source_metadata,
                )

    def _backfill_truthsocial_from_cnn_mirror(self, adapter: SourceAdapter) -> None:
        if not isinstance(adapter, TruthSocialAdapter):
            return

        has_truncated_docs = (
            self.db.query(RawDocument)
            .filter(
                RawDocument.source_name == "truthsocial",
                RawDocument.fetch_path == "index_fallback",
                RawDocument.text.like("%…"),
            )
            .first()
        )
        if has_truncated_docs is None:
            return

        try:
            mirror_result = adapter._fetch_cnn_mirror()
        except Exception:
            return
        self._repair_truthsocial_truncated_documents(adapter, mirror_result.items)

    # ------------------------------------------------------------------
    # Source ingestion stage
    # ------------------------------------------------------------------

    def ingest_sources(
        self,
        source_names: list[str] | None = None,
    ) -> list[IngestResult]:
        """Poll enabled sources, fetch new items, and store as RawDocuments.

        Args:
            source_names: Optional list of source names to limit polling.
                          If None, polls all configured adapters.

        Returns:
            List of IngestResult, one per source polled.
        """
        results: list[IngestResult] = []
        to_poll = source_names or list(self.adapters.keys())

        for name in to_poll:
            adapter = self.adapters.get(name)
            if adapter is None:
                continue

            # Get or create source definition
            source_def = self.db.query(SourceDefinition).filter_by(source_name=name).first()
            if not source_def:
                source_def = SourceDefinition(
                    source_name=name,
                    source_tier=adapter.source_tier,
                    poll_interval_seconds=adapter.poll_interval_seconds,
                )
                self.db.add(source_def)
                self.db.flush()
            elif source_def.poll_interval_seconds != adapter.poll_interval_seconds:
                source_def.poll_interval_seconds = adapter.poll_interval_seconds
                self.db.flush()

            # Get cursor — absence means this is the first poll for this source
            cursor = self.db.query(SourceCursor).filter_by(source_name=name).first()
            is_first_poll = cursor is None
            if self._should_skip_source_poll(source_def, cursor):
                results.append(
                    IngestResult(
                        source_name=name,
                        status=PollRunStatus.SUCCEEDED,
                        is_first_poll=is_first_poll,
                        skipped=True,
                    )
                )
                continue

            result = IngestResult(source_name=name, status=PollRunStatus.STARTED)
            result.is_first_poll = is_first_poll
            poll_run = SourcePollRun(source_name=name, status=PollRunStatus.STARTED)
            self.db.add(poll_run)
            self.db.flush()
            result.poll_run_id = poll_run.id

            try:
                fetch_path: str = "unknown"
                # Fetch index
                index_result = adapter.fetch_index(
                    cursor=cursor.cursor if cursor else None,
                    conditional_headers=(
                        self._build_conditional_headers(cursor) if cursor else None
                    ),
                )
                fetch_path = index_result.fetch_path
                result.items_fetched = len(index_result.items)
                if name == "truthsocial":
                    self._repair_truthsocial_truncated_documents(adapter, index_result.items)
                    self._backfill_truthsocial_from_cnn_mirror(adapter)

                # Process each item
                new_count = 0
                for item in index_result.items:
                    try:
                        # Skip the detail HTTP fetch entirely if already stored
                        if (
                            self.db.query(RawDocument)
                            .filter_by(source_name=name, external_id=item.external_id)
                            .first()
                        ) is not None:
                            continue

                        # SEC prefilter: skip items that don't match prefilter keywords
                        if name == "sec_8k" and not item.metadata.get("prefilter_match", True):
                            continue

                        raw = adapter.fetch_detail(item)
                        doc = adapter.normalize(raw)
                        if doc is None:
                            continue

                        # Compute dedupe info
                        content_hash = _hash_text(doc.text)
                        correlation_id = _uuid()[:12]

                        # Check for exact content duplicate
                        existing = (
                            self.db.query(RawDocument)
                            .filter_by(
                                source_name=name,
                                external_id=doc.external_id,
                                content_hash=content_hash,
                            )
                            .first()
                        )
                        if existing is not None:
                            continue

                        raw_doc = RawDocument(
                            id=_uuid(),
                            correlation_id=correlation_id,
                            source_name=doc.source_name,
                            source_tier=doc.source_tier,
                            fetch_path=doc.fetch_path,
                            external_id=doc.external_id,
                            canonical_url=str(doc.canonical_url),
                            title=doc.title,
                            text=doc.text,
                            content_hash=content_hash,
                            published_at=doc.published_at,
                            updated_at=doc.updated_at,
                            detected_at=doc.detected_at,
                            source_metadata=doc.source_metadata,
                            created_at=self._now(),
                        )
                        self.db.add(raw_doc)
                        self.db.flush()
                        result.raw_document_ids.append(raw_doc.id)
                        new_count += 1
                    except Exception as exc:
                        result.errors.append(f"Item {item.external_id}: {exc}")

                result.new_documents = new_count

                # Update cursor
                if not cursor:
                    cursor = SourceCursor(source_name=name)
                    self.db.add(cursor)
                cursor.cursor = index_result.cursor
                cursor.etag = index_result.etag
                cursor.last_modified = index_result.last_modified
                cursor.last_successful_poll = self._now()

                if new_count > 0 or result.items_fetched > 0:
                    result.status = PollRunStatus.SUCCEEDED
                    poll_run.status = PollRunStatus.SUCCEEDED
                else:
                    result.status = PollRunStatus.SUCCEEDED
                    poll_run.status = PollRunStatus.SUCCEEDED

            except Exception as exc:
                result.errors.append(str(exc))
                result.status = PollRunStatus.FAILED
                poll_run.status = PollRunStatus.FAILED

            poll_run.fetch_count = result.items_fetched
            poll_run.new_count = result.new_documents
            poll_run.errors = result.errors
            poll_run.fetch_path = fetch_path
            poll_run.ended_at = self._now()
            self.db.flush()
            results.append(result)

        return results

    def _should_skip_source_poll(
        self,
        source_def: SourceDefinition,
        cursor: SourceCursor | None,
    ) -> bool:
        if not source_def.enabled:
            return True
        last_polled_at = self._get_last_polled_at(source_def.source_name, cursor)
        if last_polled_at is None:
            return False
        next_due_at = last_polled_at + timedelta(
            seconds=source_def.poll_interval_seconds
        )
        return _as_utc(self._now()) < next_due_at

    def _get_last_polled_at(
        self,
        source_name: str,
        cursor: SourceCursor | None,
    ) -> datetime | None:
        latest_run = (
            self.db.query(SourcePollRun)
            .filter_by(source_name=source_name)
            .order_by(SourcePollRun.started_at.desc())
            .first()
        )
        if latest_run is not None:
            last_polled_at = latest_run.ended_at or latest_run.started_at
            return _as_utc(last_polled_at)
        if cursor is None or cursor.last_successful_poll is None:
            return None
        return _as_utc(cursor.last_successful_poll)

    # ------------------------------------------------------------------
    # Document processing stage
    # ------------------------------------------------------------------

    def process_documents(
        self,
        raw_document_ids: list[str] | None = None,
    ) -> list[ProcessingResult]:
        """Classify raw documents, resolve companies, create extracted events.

        Args:
            raw_document_ids: Specific document IDs to process, or None
                              to process all unprocessed documents.

        Returns:
            List of ProcessingResult, one per document processed.
        """
        results: list[ProcessingResult] = []

        if raw_document_ids:
            docs = (
                self.db.query(RawDocument)
                .filter(RawDocument.id.in_(raw_document_ids))
                .all()
            )
        else:
            # Find unprocessed documents (those without a ProcessingRun)
            processed_ids = {pr.raw_document_id for pr in self.db.query(ProcessingRun).all()}
            all_docs = self.db.query(RawDocument).all()
            docs = [d for d in all_docs if d.id not in processed_ids]

        for doc in docs:
            result = ProcessingResult(
                raw_document_id=doc.id,
                status=ProcessingStatus.PENDING,
            )

            try:
                # Classify
                classification = self._classify(
                    doc.title, doc.text, doc.source_metadata
                )

                # Store ProcessingRun
                prompt_info = get_prompt_info()
                proc_run = ProcessingRun(
                    id=_uuid(),
                    raw_document_id=doc.id,
                    classifier_model=classification.model,
                    prompt_version=classification.prompt_version,
                    prompt_hash=classification.prompt_hash,
                    raw_response={"content": classification.raw_response}
                    if classification.raw_response else None,
                    parsed_result=(
                        classification.parsed_result.model_dump()
                        if classification.parsed_result
                        else None
                    ),
                    tokens_in=(
                        classification.token_usage.get("prompt_tokens")
                        if classification.token_usage
                        else None
                    ),
                    tokens_out=(
                        classification.token_usage.get("completion_tokens")
                        if classification.token_usage
                        else None
                    ),
                    estimated_cost=classification.estimated_cost_usd,
                    status=classification.status,
                    error=classification.error,
                )
                self.db.add(proc_run)
                self.db.flush()
                result.processing_run_id = proc_run.id

                if classification.status != ProcessingStatus.SUCCEEDED:
                    result.status = classification.status
                    result.error = classification.error
                    results.append(result)
                    continue

                parsed = classification.parsed_result
                if parsed is None or not parsed.relevant:
                    result.status = ProcessingStatus.SUCCEEDED
                    results.append(result)
                    continue

                # Create ExtractedEvent
                extracted = ExtractedEvent(
                    id=_uuid(),
                    raw_document_id=doc.id,
                    processing_run_id=proc_run.id,
                    event_payload=parsed.model_dump(),
                )
                self.db.add(extracted)
                self.db.flush()
                result.extracted_event_id = extracted.id

                # Resolve companies
                for company in parsed.companies:
                    resolution = self.resolver.resolve(company.name)
                    best = resolution.best_candidate

                    event_company = EventCompany(
                        id=_uuid(),
                        extracted_event_id=extracted.id,
                        company_id=None,  # Will be set once we store the company
                        candidate_name=company.name,
                        mapping_confidence=best.confidence if best else 0.0,
                        mapping_status="resolved" if best and not resolution.ambiguous else "ambiguous",
                    )
                    self.db.add(event_company)
                    result.event_company_ids.append(event_company.id)

                self.db.flush()
                result.status = ProcessingStatus.SUCCEEDED

            except Exception as exc:
                result.status = ProcessingStatus.FAILED
                result.error = str(exc)

            results.append(result)

        return results

    # ------------------------------------------------------------------
    # Signal creation stage
    # ------------------------------------------------------------------

    def create_signals(
        self,
        extracted_event_ids: list[str] | None = None,
    ) -> list[SignalResult]:
        """Score extracted events, create/update canonical signal events.

        Applies scoring, fingerprinting, deduplication, and cooldown logic.
        Only creates signal events when they are not IGNORE and not on cooldown.

        Args:
            extracted_event_ids: Specific extracted event IDs to process,
                                 or None to process all unprocessed.

        Returns:
            List of SignalResult, one per signal decision.
        """
        results: list[SignalResult] = []

        all_extracted = self.db.query(ExtractedEvent).all()

        if extracted_event_ids:
            events = [
                e for e in all_extracted if e.id in extracted_event_ids
            ]
        else:
            events = all_extracted

        for extracted in events:
            sr = SignalResult(extracted_event_id=extracted.id)
            payload = extracted.event_payload or {}
            event_type = payload.get("event_type", "irrelevant")
            direction_str = payload.get("direction", "neutral")
            sr.event_type = event_type
            sr.direction = Direction(direction_str)

            # Get the best company mapping
            event_companies = (
                self.db.query(EventCompany)
                .filter_by(extracted_event_id=extracted.id)
                .all()
            )
            best_mapping = 0.0
            ticker = ""
            company_name = ""
            for ec in event_companies:
                company_name = ec.candidate_name or company_name
                if ec.mapping_confidence > best_mapping:
                    best_mapping = ec.mapping_confidence
                    ticker = ec.candidate_name  # fallback; real ticker from resolution

            # Try to get a real ticker if available from resolver
            if event_companies and company_name:
                resolution = self.resolver.resolve(company_name)
                best = resolution.best_candidate
                if best and best.ticker:
                    ticker = best.ticker
                if best:
                    best_mapping = max(best_mapping, best.confidence)

            # Bug #7: Compute staleness from published_at vs detected_at
            raw_doc = self.db.query(RawDocument).filter_by(id=extracted.raw_document_id).first()
            is_stale = False
            if raw_doc and raw_doc.published_at and raw_doc.detected_at:
                pub = raw_doc.published_at
                det = raw_doc.detected_at
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if det.tzinfo is None:
                    det = det.replace(tzinfo=timezone.utc)
                is_stale = (det - pub) > timedelta(hours=24)

            # Bug #9: Determine secondary-source and fetch-path-delay flags
            is_secondary_source = False
            fetch_path_has_delay = False
            if raw_doc:
                fetch_path_val = raw_doc.fetch_path or ""
                if fetch_path_val in ("cnn_mirror", "mirror", "secondary"):
                    fetch_path_has_delay = True
                    is_secondary_source = True
                elif raw_doc.source_tier == SourceTier.TIER_2:
                    is_secondary_source = True

            # Score the event
            score_ctx = ScoreContext(
                event_type=event_type,
                direction=direction_str,
                strength=payload.get("strength", 1),
                classifier_confidence=payload.get("confidence", 0.0),
                mapping_confidence=best_mapping,
                source_tier=self._get_source_tier(extracted),
                active_public_ticker=bool(ticker),
                market_snapshot_available=True,  # Actual downgrade check deferred to create_alerts
                is_stale=is_stale,
                is_secondary_source=is_secondary_source,
                fetch_path_has_delay=fetch_path_has_delay,
            )

            decision = compute_actionability(score_ctx)
            sr.alert_level = decision.alert_level
            sr.catalyst_score = decision.catalyst_score

            if decision.alert_level == AlertLevel.IGNORE:
                sr.skipped = True
                sr.reason = "IGNORE: event is irrelevant or has no active ticker"
                results.append(sr)
                continue

            # Compute event fingerprint
            published_date = None
            if raw_doc and raw_doc.published_at:
                published_date = raw_doc.published_at.date()

            award_ids = payload.get("award_or_contract_ids", []) or []
            monetary_amounts = payload.get("monetary_amounts", []) or []

            fingerprint = compute_event_fingerprint(
                sorted_ciks_or_tickers=[ticker] if ticker else [],
                event_type=event_type,
                direction=direction_str,
                action_status=payload.get("action_status", "unknown"),
                award_or_contract_ids=award_ids,
                monetary_amounts=monetary_amounts,
                published_date=published_date,
            )

            # Bug #2 fix: Never attempt to insert a duplicate fingerprint row.
            # An exact fingerprint match means the canonical event already exists;
            # trying to re-insert it after cooldown expiry causes IntegrityError.
            existing_exact = self.db.query(SignalEvent).filter_by(fingerprint=fingerprint).first()
            if existing_exact is not None:
                sr.is_duplicate = True
                sr.skipped = True
                sr.reason = "Exact duplicate fingerprint"
                results.append(sr)
                continue

            # Bug #3 fix: Check cooldown by (ticker, event_type, direction) not fingerprint.
            # The previous code only checked the exact fingerprint for cooldown, so events
            # with different amounts/IDs (but same key) bypassed the anti-spam window.
            cooldown_key = CooldownKey(
                ticker=ticker or "UNKNOWN",
                event_type=event_type,
                direction=direction_str,
            )
            recent_same_key = (
                self.db.query(SignalEvent)
                .filter(
                    SignalEvent.event_type == event_type,
                    SignalEvent.direction == Direction(direction_str),
                )
                .order_by(SignalEvent.created_at.desc())
                .all()
            )
            recent_sig = None
            for sig in recent_same_key:
                if (sig.payload or {}).get("ticker") == ticker:
                    recent_sig = sig
                    break

            if recent_sig is not None:
                last_alerted = recent_sig.created_at
                if last_alerted.tzinfo is None:
                    last_alerted = last_alerted.replace(tzinfo=timezone.utc)
                cooldown_state = CooldownState(
                    key=cooldown_key,
                    last_alerted_at=last_alerted,
                    fingerprint=fingerprint,
                )
                if cooldown_state.remaining_seconds > 0:
                    # Bug #4 fix: Check for material update before suppressing
                    prev_payload = recent_sig.payload or {}
                    new_event_dict = {
                        "direction": direction_str,
                        "action_status": payload.get("action_status", ""),
                        "award_or_contract_ids": award_ids,
                        "monetary_amounts": monetary_amounts,
                        "catalyst_score": decision.catalyst_score,
                        "alert_level": decision.alert_level.value,
                    }
                    material = is_material_update(prev_payload, new_event_dict)
                    if not material.is_material:
                        sr.is_duplicate = True
                        sr.on_cooldown = True
                        sr.skipped = True
                        sr.reason = "On cooldown — not a material update"
                        results.append(sr)
                        continue

            # Create or update signal event
            signal = SignalEvent(
                id=_uuid(),
                fingerprint=fingerprint,
                event_type=event_type,
                direction=Direction(direction_str),
                action_status=payload.get("action_status", "unknown"),
                catalyst_score=decision.catalyst_score,
                classifier_confidence=payload.get("confidence", 0.0),
                alert_level=decision.alert_level,
                primary_company_id=None,
                payload={
                    "ticker": ticker,
                    "direction": direction_str,
                    "rationale": payload.get("rationale", ""),
                    "risks": payload.get("risks", []),
                    "companies": [c.get("name", "") for c in payload.get("companies", [])],
                    "award_or_contract_ids": award_ids,
                    "monetary_amounts": monetary_amounts,
                    "extracted_event_ids": [extracted.id],
                },
                published_bucket=(
                    published_date.isoformat() if published_date else ""
                ),
            )
            self.db.add(signal)
            self.db.flush()
            sr.signal_event_id = signal.id

            # Create event evidence
            evidence_items = payload.get("evidence", []) or []
            for ev in evidence_items[:10]:  # limit evidence to 10 snippets
                evidence = EventEvidence(
                    id=_uuid(),
                    signal_event_id=signal.id,
                    raw_document_id=extracted.raw_document_id,
                    evidence_text=ev.get("text", ""),
                    start_offset=ev.get("start_offset", 0),
                    end_offset=ev.get("end_offset", 0),
                )
                self.db.add(evidence)

            self.db.flush()
            results.append(sr)

        return results

    # ------------------------------------------------------------------
    # Alert creation stage
    # ------------------------------------------------------------------

    def create_alerts(
        self,
        signal_event_ids: list[str] | None = None,
    ) -> list[AlertResult]:
        """Create alert payloads and outbox entries for signal events.

        Rules:
        - WATCH alerts: persist signal only, no alert/outbox
        - IGNORE: skip entirely
        - TRADEABLE, REVIEW, AVOID_CHASE: create alert and outbox

        Args:
            signal_event_ids: Specific signal IDs to create alerts for,
                              or None for all signals without alerts.

        Returns:
            List of AlertResult, one per signal event processed.
        """
        results: list[AlertResult] = []

        # Find signals that don't have alerts yet
        existing_alert_signal_ids = {
            a.signal_event_id for a in self.db.query(Alert).all()
        }

        if signal_event_ids:
            signals = (
                self.db.query(SignalEvent)
                .filter(SignalEvent.id.in_(signal_event_ids))
                .all()
            )
        else:
            all_signals = self.db.query(SignalEvent).all()
            signals = [
                s for s in all_signals
                if s.id not in existing_alert_signal_ids
            ]

        for signal in signals:
            ar = AlertResult(
                signal_event_id=signal.id,
                alert_level=signal.alert_level,
            )

            # WATCH: persist only, no delivery
            if signal.alert_level == AlertLevel.WATCH:
                ar.skipped = True
                ar.reason = "WATCH: internal only, no delivery"
                results.append(ar)
                continue

            # IGNORE: skip entirely
            if signal.alert_level == AlertLevel.IGNORE:
                ar.skipped = True
                ar.reason = "IGNORE: skipped"
                results.append(ar)
                continue

            # Get the raw document and its data for context
            raw_doc = self._get_raw_doc_for_signal(signal)
            event_companies = self._get_event_companies_for_signal(signal)

            # Determine ticker and company name
            ticker, company_name = "", ""
            for ec in event_companies:
                company_name = ec.candidate_name or company_name
                resolution = self.resolver.resolve(company_name) if company_name else None
                best = resolution.best_candidate if resolution else None
                if best and best.ticker:
                    ticker = best.ticker
                    break
            if not ticker and company_name:
                ticker = company_name

            # Take market snapshot
            snapshot = None
            ms = None
            if ticker:
                try:
                    snapshot = self._snapshot(ticker)
                    # Persist snapshot
                    ms = MarketSnapshot(
                        id=_uuid(),
                        ticker=snapshot.ticker,
                        provider=snapshot.provider,
                        feed=snapshot.feed,
                        request_time=snapshot.request_time,
                        observed_at=snapshot.observed_at,
                        price=snapshot.price,
                        previous_close=snapshot.previous_close,
                        intraday_move_pct=snapshot.intraday_move_pct,
                        market_status=snapshot.market_status,
                        volume=snapshot.volume,
                        quality_flags=snapshot.quality_flags,
                        label=snapshot.label,
                    )
                    self.db.add(ms)
                    self.db.flush()
                except Exception:
                    pass  # No snapshot available

            # Apply market-data downgrade using the actual IEX snapshot.
            # Market data may only downgrade, never promote.
            downgrade_result = apply_market_downgrade(
                signal.alert_level,
                snapshot,
                is_bearish=signal.direction == Direction.BEARISH,
            )
            signal.alert_level = downgrade_result.downgraded_level

            # Collect prior bullish signals for the same company/ticker
            prior_bullish = self._collect_prior_bullish(signal, ticker, company_name)

            # Build render context
            payload_data = signal.payload or {}
            decision = SignalDecision(
                alert_level=signal.alert_level,
                catalyst_score=signal.catalyst_score,
                direction=signal.direction,
                modifiers=[],
                reasons=[
                    f"Base score: {signal.catalyst_score} from {signal.event_type}",
                ],
            )
            render_ctx = AlertRenderContext(
                alert_id="",  # filled after alert creation
                level=signal.alert_level,
                decision=decision,
                ticker=ticker,
                company_name=company_name,
                event_type=signal.event_type,
                direction=signal.direction,
                source_name=raw_doc.source_name if raw_doc else "unknown",
                source_url=_resolve_alert_source_url(raw_doc),
                fetch_path=raw_doc.fetch_path if raw_doc else "unknown",
                published_at=raw_doc.published_at if raw_doc else None,
                detected_at=raw_doc.detected_at if raw_doc else None,
                rationale=payload_data.get("rationale", ""),
                evidence=self._get_evidence_texts(signal),
                risks=payload_data.get("risks", []),
                classifier_confidence=signal.classifier_confidence,
                mapping_confidence=self._get_best_mapping(event_companies),
                market_snapshot=snapshot,
                prior_bullish_signals=prior_bullish,
            )

            # Render alert payload
            alert_id = _uuid()
            render_ctx.alert_id = alert_id

            try:
                rendered = render_alert_payload(render_ctx)
            except ValueError as exc:
                # WATCH alerts are blocked by the renderer — should not happen here
                ar.skipped = True
                ar.reason = f"Render blocked: {exc}"
                results.append(ar)
                continue

            # Create Alert
            alert = Alert(
                id=alert_id,
                signal_event_id=signal.id,
                market_snapshot_id=ms.id if snapshot else None,
                level=signal.alert_level,
                rendered_payload=rendered.model_dump(mode="json"),
                score_components={
                    "catalyst_score": signal.catalyst_score,
                    "confidence": signal.classifier_confidence,
                    "direction": signal.direction.value,
                },
                dedupe_key=rendered.dedupe_key,
                created_at=self._now(),
            )
            self.db.add(alert)

            # Create AlertOutbox in the same transaction
            idempotency_key = generate_idempotency_key(alert_id, 0)
            outbox = AlertOutbox(
                id=_uuid(),
                alert_id=alert_id,
                status=DeliveryStatusEnum.PENDING,
                idempotency_key=idempotency_key,
            )
            self.db.add(outbox)
            self.db.flush()

            ar.alert_id = alert_id
            ar.outbox_id = outbox.id
            ar.delivery_ready = alert.level in (AlertLevel.TRADEABLE, AlertLevel.AVOID_CHASE)

            # Bug #5: Create paper trade entry for actionable alerts (spec §13)
            if ticker:
                paper = make_paper_entry(
                    ticker=ticker,
                    direction=signal.direction,
                    alert_level=signal.alert_level,
                    snapshot=snapshot,
                )
                if paper.notional_eur > 0:
                    paper_trade = PaperTrade(
                        id=_uuid(),
                        alert_id=alert_id,
                        ticker=paper.ticker,
                        direction=paper.direction,
                        notional_eur=paper.notional_eur,
                        entry_price=paper.entry_price,
                        entry_time=paper.entry_time,
                        provider=paper.provider,
                        feed=paper.feed,
                        quality={"quality_flags": paper.quality_flags},
                    )
                    self.db.add(paper_trade)

            results.append(ar)

        return results

    # ------------------------------------------------------------------
    # Delivery stage
    # ------------------------------------------------------------------

    def deliver_pending(self) -> list[DeliveryAttemptResult]:
        """Claim and deliver pending outbox entries.

        Uses at-most-once delivery: UNKNOWN entries are not blindly retried.
        Only PENDING and (stale) CLAIMED entries are picked up.

        Returns:
            List of DeliveryAttemptResult, one per outbox entry processed.
        """
        results: list[DeliveryAttemptResult] = []

        chat_id = self.settings.telegram_owner_id
        if chat_id == 0:
            chat_id = 0  # No delivery if no owner ID configured

        # Find all pending / stale-claimed entries
        pending_entries = (
            self.db.query(AlertOutbox)
            .filter(
                AlertOutbox.status.in_((
                    DeliveryStatusEnum.PENDING,
                    DeliveryStatusEnum.CLAIMED,
                ))
            )
            .with_for_update(skip_locked=True)
            .all()
        )

        for entry in pending_entries:
            # Follow the outbox state machine
            outbox_entry = OutboxEntry(
                id=entry.id,
                alert_id=entry.alert_id,
                status=entry.status,
                idempotency_key=entry.idempotency_key,
                claimed_at=entry.claimed_at,
                sent_at=entry.sent_at,
            )

            claim = claim_outbox(outbox_entry)
            if claim.value not in ("claimed",):
                continue  # Already claimed, sent, or UNKNOWN

            dar = DeliveryAttemptResult(
                outbox_id=entry.id,
                alert_id=entry.alert_id,
            )

            # Load the rendered payload
            alert = self.db.query(Alert).filter_by(id=entry.alert_id).first()
            if alert is None:
                dar.status = DeliveryStatusEnum.FAILED
                dar.error = "Alert not found"
                entry.status = DeliveryStatusEnum.FAILED
                self._record_delivery(entry, None, DeliveryStatusEnum.FAILED)
                results.append(dar)
                continue

            rendered = alert.rendered_payload or {}
            from gktrader.domain.contracts import AlertPayload
            try:
                payload = AlertPayload.model_validate(rendered)
            except Exception:
                dar.status = DeliveryStatusEnum.FAILED
                dar.error = "Invalid rendered payload"
                entry.status = DeliveryStatusEnum.FAILED
                self._record_delivery(entry, None, DeliveryStatusEnum.FAILED)
                results.append(dar)
                continue

            # Mark claimed
            entry.claimed_at = self._now()
            entry.status = DeliveryStatusEnum.CLAIMED
            self.db.flush()

            # Send main alert
            try:
                status = self._deliver(self.settings, chat_id, payload)
            except Exception as exc:
                status = DeliveryStatus.FAILED if str(exc) != "timeout" else DeliveryStatus.UNKNOWN

            dar.status = status

            if status in (DeliveryStatus.SENT,):
                # Try to get message_id from response (not directly available in status)
                msg_id = None
                entry.status = DeliveryStatusEnum.SENT
                entry.sent_at = self._now()
                self._record_delivery(entry, msg_id, DeliveryStatusEnum.SENT)

                # Send continuation messages
                if payload.continuation_messages:
                    try:
                        self._continue_deliver(
                            self.settings,
                            chat_id,
                            payload.continuation_messages,
                        )
                    except Exception:
                        pass  # Best-effort for continuations

            elif status in (DeliveryStatus.UNKNOWN,):
                entry.status = DeliveryStatusEnum.UNKNOWN
                entry.sent_at = self._now()
                self._record_delivery(entry, None, DeliveryStatusEnum.UNKNOWN)

            else:
                entry.status = DeliveryStatusEnum.FAILED
                self._record_delivery(entry, None, DeliveryStatusEnum.FAILED)

            self.db.flush()
            results.append(dar)

        return results

    # ------------------------------------------------------------------
    # Full pipeline run
    # ------------------------------------------------------------------

    def run_full_pipeline(
        self,
        source_names: list[str] | None = None,
    ) -> PipelineResult:
        """Run the complete pipeline: ingest → process → signal → alert → deliver.

        All stages are recorded. The caller should commit the transaction
        after this returns.

        Args:
            source_names: Optional source name filter for ingestion.

        Returns:
            PipelineResult with aggregate results.
        """
        result = PipelineResult(started_at=self._now())

        # Stage 1: Ingest sources
        ingest_results = self.ingest_sources(source_names)
        result.ingest_results = ingest_results
        self.db.flush()

        # Stage 2: Process new documents
        # Bug #6: On the first poll for a source, baseline documents are stored
        # but skipped (no signals/alerts) unless allow_alerts_during_replay is set.
        baseline_active = (
            self.settings.enable_first_start_baseline
            and not self.settings.allow_alerts_during_replay
        )
        new_doc_ids = []
        baseline_doc_ids: set[str] = set()
        for ir in ingest_results:
            if baseline_active and ir.is_first_poll:
                baseline_doc_ids.update(ir.raw_document_ids)
            else:
                new_doc_ids.extend(ir.raw_document_ids)
        # Only process docs that are not baseline-suppressed; skip entirely if none.
        if new_doc_ids:
            processing_results = self.process_documents(new_doc_ids)
        else:
            processing_results = []
        result.processing_results = processing_results
        self.db.flush()

        # Stage 3: Create signals
        new_extracted_ids = [
            pr.extracted_event_id
            for pr in processing_results
            if pr.extracted_event_id
        ]
        signal_results = self.create_signals(new_extracted_ids)
        result.signal_results = signal_results
        self.db.flush()

        # Stage 4: Create alerts
        new_signal_ids = [
            sr.signal_event_id
            for sr in signal_results
            if sr.signal_event_id and not sr.skipped
        ]
        alert_results = self.create_alerts(new_signal_ids)
        result.alert_results = alert_results
        self.db.flush()

        # Stage 5: Deliver
        self.deliver_pending()
        self.db.flush()

        result.completed_at = self._now()
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_conditional_headers(
        self, cursor: SourceCursor
    ) -> dict[str, str] | None:
        """Build conditional HTTP headers from cursor data."""
        headers: dict[str, str] = {}
        if cursor.etag:
            headers["If-None-Match"] = cursor.etag
        if cursor.last_modified:
            headers["If-Modified-Since"] = cursor.last_modified
        return headers or None

    def _default_snapshot(self, ticker: str) -> MarketSnapshotContract:
        """Default market snapshot using Alpaca IEX provider."""
        from gktrader.domain.enums import MarketStatus
        from gktrader.marketdata.alpaca import AlpacaIEXProvider

        key = self.settings.alpaca_api_key
        secret = self.settings.alpaca_api_secret
        if not key or not secret:
            return MarketSnapshotContract(
                ticker=ticker,
                provider="alpaca",
                feed="IEX",
                observed_at=self._now(),
                request_time=self._now(),
                market_status=MarketStatus.UNKNOWN,
                quality_flags=["no_alpaca_credentials"],
                label="IEX partial-market data",
            )

        provider = AlpacaIEXProvider(
            api_key=key,
            api_secret=secret,
        )
        try:
            return provider.snapshot(ticker)
        finally:
            provider.close()

    def _get_source_tier(self, extracted: ExtractedEvent) -> str:
        """Get the source tier string for an extracted event."""
        raw_doc = (
            self.db.query(RawDocument)
            .filter_by(id=extracted.raw_document_id)
            .first()
        )
        if raw_doc and raw_doc.source_tier:
            return raw_doc.source_tier.value
        return "tier_1"

    def _get_raw_doc_for_signal(self, signal: SignalEvent) -> RawDocument | None:
        """Find the raw document associated with a signal event."""
        payload = signal.payload or {}
        extracted_ids = payload.get("extracted_event_ids", [])
        if extracted_ids:
            extracted = (
                self.db.query(ExtractedEvent)
                .filter_by(id=extracted_ids[0])
                .first()
            )
            if extracted:
                return (
                    self.db.query(RawDocument)
                    .filter_by(id=extracted.raw_document_id)
                    .first()
                )
        return None

    def _get_event_companies_for_signal(
        self, signal: SignalEvent
    ) -> list[EventCompany]:
        """Get event company records for a signal event."""
        payload = signal.payload or {}
        extracted_ids = payload.get("extracted_event_ids", [])
        if not extracted_ids:
            return []
        return (
            self.db.query(EventCompany)
            .filter(EventCompany.extracted_event_id.in_(extracted_ids))
            .all()
        )

    def _get_evidence_texts(self, signal: SignalEvent) -> list[str]:
        """Get evidence text snippets for a signal event."""
        evidence = (
            self.db.query(EventEvidence)
            .filter_by(signal_event_id=signal.id)
            .all()
        )
        return [e.evidence_text for e in evidence if e.evidence_text]

    def _get_best_mapping(self, event_companies: list[EventCompany]) -> float:
        """Get the best mapping confidence from event companies."""
        if not event_companies:
            return 0.0
        return max(ec.mapping_confidence for ec in event_companies)

    def _collect_prior_bullish(
        self, signal: SignalEvent, ticker: str, company_name: str = ""
    ) -> list[PriorBullishSignal]:
        """Collect prior bullish signals for the same validated company/ticker.

        Only returns bullish signals whose payload company list matches the
        current bearish signal's *company_name*, ensuring history is scoped
        to the same company, not all bullish signals globally.
        """
        if signal.direction != Direction.BEARISH:
            return []

        if not company_name:
            return []

        # Find prior bullish signals for the same company
        prior = (
            self.db.query(SignalEvent)
            .filter(
                SignalEvent.direction == Direction.BULLISH,
                SignalEvent.id != signal.id,
            )
            .order_by(SignalEvent.created_at.desc())
            .all()
        )

        results: list[PriorBullishSignal] = []
        for p in prior:
            payload = p.payload or {}
            companies = payload.get("companies", [])
            # Only include signals whose payload mentions the same company
            if company_name not in companies:
                continue
            results.append(
                PriorBullishSignal(
                    source_date=p.created_at,
                    event_type=p.event_type,
                    alert_level=p.alert_level,
                    rationale=payload.get("rationale", ""),
                )
            )
        return results

    def _record_delivery(
        self,
        outbox: AlertOutbox,
        message_id: str | None,
        status: DeliveryStatusEnum,
    ) -> None:
        """Record a delivery attempt in alert_deliveries."""
        delivery = AlertDelivery(
            id=_uuid(),
            alert_id=outbox.alert_id,
            request_payload={},
            response_payload={},
            message_id=message_id,
            status=status,
        )
        self.db.add(delivery)
