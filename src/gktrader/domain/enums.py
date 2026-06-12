from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    PRESIDENTIAL_POSITIVE_MENTION = "presidential_positive_mention"
    PRESIDENTIAL_NEGATIVE_MENTION = "presidential_negative_mention"
    GOVERNMENT_FUNDING = "government_funding"
    GOVERNMENT_EQUITY_STAKE = "government_equity_stake"
    GOVERNMENT_CONTRACT = "government_contract"
    REGULATORY_TAILWIND = "regulatory_tailwind"
    REGULATORY_HEADWIND = "regulatory_headwind"
    OGE_PURCHASE_DISCLOSURE = "oge_purchase_disclosure"
    OGE_SALE_DISCLOSURE = "oge_sale_disclosure"
    COMPANY_CONFIRMATION_8K = "company_confirmation_8k"
    SECTOR_ONLY_MENTION = "sector_only_mention"
    IRRELEVANT = "irrelevant"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNCLEAR = "unclear"


class AlertLevel(StrEnum):
    WATCH = "WATCH"
    REVIEW = "REVIEW"
    TRADEABLE = "TRADEABLE"
    AVOID_CHASE = "AVOID_CHASE"
    IGNORE = "IGNORE"


class SourceTier(StrEnum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    FALLBACK = "fallback"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INVALID = "invalid"


class PollRunStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SENT = "sent"
    UNKNOWN = "unknown"
    FAILED = "failed"


class MarketStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    PREMARKET = "premarket"
    AFTER_HOURS = "after_hours"
    UNKNOWN = "unknown"


class TradeDecisionType(StrEnum):
    BOUGHT = "bought"
    SOLD_REDUCED = "sold_reduced"
    SHORTED = "shorted"
    NO_TRADE = "no_trade"


class PositionEventType(StrEnum):
    OPEN = "open"
    INCREASE = "increase"
    REDUCE = "reduce"
    CLOSE = "close"
    CONFIRM = "confirm"
    ADJUST = "adjust"


class InteractionStateType(StrEnum):
    AWAITING_AMOUNT = "awaiting_amount"
    AWAITING_POSITION_CONFIRMATION = "awaiting_position_confirmation"
    SNOOZE_REMINDER = "snooze_reminder"
