from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from gktrader.db.base import Base
from gktrader.domain.enums import (
    AlertLevel,
    DeliveryStatus,
    Direction,
    InteractionStateType,
    MarketStatus,
    PollRunStatus,
    PositionEventType,
    ProcessingStatus,
    SourceTier,
    TradeDecisionType,
)


def uuid_str() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class SourceDefinition(Base):
    __tablename__ = "source_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    source_tier: Mapped[SourceTier] = mapped_column(Enum(SourceTier), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    health_thresholds: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SourceCursor(Base):
    __tablename__ = "source_cursors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    cursor: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    last_successful_poll: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourcePollRun(Base):
    __tablename__ = "source_poll_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_name: Mapped[str] = mapped_column(String(100), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[PollRunStatus] = mapped_column(Enum(PollRunStatus), nullable=False)
    fetch_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    fetch_path: Mapped[str | None] = mapped_column(String(100))


class RawDocument(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("source_name", "external_id", "content_hash", name="uq_raw_document_version"),
        Index("ix_raw_documents_detected_at", "detected_at"),
        Index("ix_raw_documents_published_at", "published_at"),
        Index("ix_raw_documents_source_name", "source_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_tier: Mapped[SourceTier] = mapped_column(Enum(SourceTier), nullable=False)
    fetch_path: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    raw_document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    classifier_model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSON)
    parsed_result: Mapped[dict | None] = mapped_column(JSON)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    status: Mapped[ProcessingStatus] = mapped_column(Enum(ProcessingStatus), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    cik: Mapped[str | None] = mapped_column(String(20), index=True)
    ticker: Mapped[str | None] = mapped_column(String(20), index=True)
    exchange: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (UniqueConstraint("normalized_alias", "provenance", name="uq_alias_provenance"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), index=True)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    provenance: Mapped[str] = mapped_column(String(100), nullable=False)
    review_state: Mapped[str] = mapped_column(String(50), default="approved", nullable=False)


class ExtractedEvent(Base):
    __tablename__ = "extracted_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    raw_document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    processing_run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), index=True)
    event_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class EventCompany(Base):
    __tablename__ = "event_companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    extracted_event_id: Mapped[str] = mapped_column(ForeignKey("extracted_events.id"), index=True)
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), index=True)
    candidate_name: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    mapping_status: Mapped[str] = mapped_column(String(50), nullable=False)


class SignalEvent(Base):
    __tablename__ = "signal_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[Direction] = mapped_column(Enum(Direction), nullable=False)
    action_status: Mapped[str] = mapped_column(String(100), nullable=False)
    catalyst_score: Mapped[int] = mapped_column(Integer, nullable=False)
    classifier_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    alert_level: Mapped[AlertLevel] = mapped_column(Enum(AlertLevel), nullable=False)
    primary_company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    published_bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class EventEvidence(Base):
    __tablename__ = "event_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    signal_event_id: Mapped[str] = mapped_column(ForeignKey("signal_events.id"), index=True)
    raw_document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    feed: Mapped[str] = mapped_column(String(50), nullable=False)
    request_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[float | None] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float)
    intraday_move_pct: Mapped[float | None] = mapped_column(Float)
    market_status: Mapped[MarketStatus] = mapped_column(Enum(MarketStatus), nullable=False)
    volume: Mapped[int | None] = mapped_column(Integer)
    quality_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    label: Mapped[str] = mapped_column(String(100), default="IEX partial-market data", nullable=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    signal_event_id: Mapped[str] = mapped_column(ForeignKey("signal_events.id"), index=True)
    market_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("market_snapshots.id"), index=True)
    level: Mapped[AlertLevel] = mapped_column(Enum(AlertLevel), nullable=False)
    rendered_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    score_components: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AlertOutbox(Base):
    __tablename__ = "alert_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), unique=True, index=True)
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus), default=DeliveryStatus.PENDING)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), index=True)
    request_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    response_payload: Mapped[dict | None] = mapped_column(JSON)
    message_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[DeliveryStatus] = mapped_column(Enum(DeliveryStatus), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TradeDecision(Base):
    __tablename__ = "trade_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id"), nullable=True, index=True)
    decision: Mapped[TradeDecisionType] = mapped_column(Enum(TradeDecisionType), nullable=False)
    amount_eur: Mapped[float | None] = mapped_column(Float)
    execution_price: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class PositionEvent(Base):
    __tablename__ = "position_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    event_type: Mapped[PositionEventType] = mapped_column(Enum(PositionEventType), nullable=False)
    direction: Mapped[Direction] = mapped_column(Enum(Direction), nullable=False)
    amount_eur: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    source_alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction), nullable=False)
    net_amount_eur: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    average_price: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class InteractionState(Base):
    __tablename__ = "interaction_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    state_type: Mapped[InteractionStateType] = mapped_column(Enum(InteractionStateType), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    alert_id: Mapped[str | None] = mapped_column(ForeignKey("alerts.id"), index=True)
    feedback_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    report_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction), nullable=False)
    notional_eur: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Float)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str | None] = mapped_column(String(50))
    feed: Mapped[str | None] = mapped_column(String(50))
    quality: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    paper_trade_id: Mapped[str] = mapped_column(ForeignKey("paper_trades.id"), index=True)
    horizon: Mapped[str] = mapped_column(String(10), nullable=False)
    return_pct: Mapped[float | None] = mapped_column(Float)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    max_runup_pct: Mapped[float | None] = mapped_column(Float)
    missing_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quality: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
