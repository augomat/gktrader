from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from gktrader.domain.enums import (
    AlertLevel,
    Direction,
    EventType,
    MarketStatus,
    PositionEventType,
    SourceTier,
    TradeDecisionType,
)


class EvidenceSnippet(BaseModel):
    text: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)

    @field_validator("end_offset")
    @classmethod
    def validate_offsets(cls, value: int, info: Any) -> int:
        start = info.data.get("start_offset", 0)
        if value <= start:
            raise ValueError("end_offset must be greater than start_offset")
        return value


class ClassifierCompany(BaseModel):
    name: str = Field(min_length=1)


class ClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant: bool
    event_type: EventType
    direction: Direction
    strength: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    companies: list[ClassifierCompany]
    rationale: str = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    action_status: str = Field(min_length=1)
    monetary_amounts: list[str] = Field(default_factory=list)
    award_or_contract_ids: list[str] = Field(default_factory=list)
    government_actors: list[str] = Field(default_factory=list)
    evidence: list[EvidenceSnippet] = Field(min_length=1)


class NormalizedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    source_tier: SourceTier
    fetch_path: str
    external_id: str
    canonical_url: HttpUrl
    title: str
    text: str
    published_at: datetime | None = None
    updated_at: datetime | None = None
    detected_at: datetime
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class SourceIndexItem(BaseModel):
    external_id: str
    detail_url: HttpUrl
    title: str
    published_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FetchIndexResult(BaseModel):
    items: list[SourceIndexItem]
    cursor: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    fetch_path: str


class TickerCandidate(BaseModel):
    company_name: str
    normalized_name: str
    ticker: str | None = None
    cik: str | None = None
    exchange: str | None = None
    is_active: bool = False
    is_public: bool = False
    confidence: float = Field(ge=0, le=1)
    provenance: str


class MarketSnapshotContract(BaseModel):
    ticker: str
    provider: str
    feed: str = "IEX"
    observed_at: datetime
    request_time: datetime
    price: float | None = None
    previous_close: float | None = None
    intraday_move_pct: float | None = None
    market_status: MarketStatus = MarketStatus.UNKNOWN
    volume: int | None = None
    quality_flags: list[str] = Field(default_factory=list)
    label: str = "IEX partial-market data"


class SignalDecision(BaseModel):
    alert_level: AlertLevel
    catalyst_score: int
    direction: Direction
    modifiers: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class PriorBullishSignal(BaseModel):
    source_date: datetime
    event_type: EventType
    alert_level: AlertLevel
    rationale: str


class AlertButton(BaseModel):
    text: str
    callback_data: str | None = None
    url: HttpUrl | None = None


class AlertPayload(BaseModel):
    alert_id: str
    level: AlertLevel
    text: str
    continuation_messages: list[str] = Field(default_factory=list)
    buttons: list[list[AlertButton]] = Field(default_factory=list)
    dedupe_key: str
    ticker: str = ""
    company: str = ""


class AlertDecisionRequest(BaseModel):
    decision: TradeDecisionType
    amount_eur: float | None = Field(default=None, gt=0)
    execution_price: float | None = Field(default=None, gt=0)
    notes: str | None = None


class AlertDecisionResponse(BaseModel):
    alert_id: str
    decision_id: str
    position_event_id: str | None = None
    status: Literal["recorded"] = "recorded"


class SnoozeAlertRequest(BaseModel):
    minutes: int = Field(default=30, ge=1, le=1440)


class PositionEventRequest(BaseModel):
    ticker: str
    event_type: PositionEventType
    amount_eur: float | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, gt=0)
    notes: str | None = None


class PositionSummary(BaseModel):
    ticker: str
    direction: Direction
    net_amount_eur: float
    average_price: float | None = None
    updated_at: datetime


class CompanyHistoryResponse(BaseModel):
    ticker: str
    signals: list[PriorBullishSignal]


class WeeklyReviewPosition(BaseModel):
    position_id: str
    ticker: str
    direction: Direction
    net_amount_eur: float
    status: str


class WeeklyReviewResponse(BaseModel):
    generated_at: datetime
    summary: str
    positions: list[WeeklyReviewPosition]


class PositionConfirmationRequest(BaseModel):
    action: Literal["keep_open", "close", "adjust"]
    amount_eur: float | None = Field(default=None, ge=0)
