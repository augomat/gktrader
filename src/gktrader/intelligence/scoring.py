"""Catalyst scoring, modifiers, and actionability determination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gktrader.domain.contracts import SignalDecision
from gktrader.domain.enums import AlertLevel, Direction, EventType, SourceTier

# ------------------------------------------------------------------
# Base catalyst scores by event type
# ------------------------------------------------------------------

_EVENT_BASE_SCORES: dict[str, int] = {
    EventType.GOVERNMENT_EQUITY_STAKE.value: 5,
    EventType.GOVERNMENT_FUNDING.value: 5,
    EventType.GOVERNMENT_CONTRACT.value: 5,
    EventType.REGULATORY_HEADWIND.value: 4,
    EventType.REGULATORY_TAILWIND.value: 4,
    EventType.PRESIDENTIAL_POSITIVE_MENTION.value: 3,
    EventType.PRESIDENTIAL_NEGATIVE_MENTION.value: 3,
    EventType.OGE_PURCHASE_DISCLOSURE.value: 3,
    EventType.OGE_SALE_DISCLOSURE.value: 3,
    EventType.COMPANY_CONFIRMATION_8K.value: 2,
    EventType.SECTOR_ONLY_MENTION.value: 1,
    EventType.IRRELEVANT.value: 0,
}


def get_base_catalyst_score(event_type: str) -> int:
    """Return the base catalyst score for an event type."""
    return _EVENT_BASE_SCORES.get(event_type, 0)


# ------------------------------------------------------------------
# Modifier descriptions
# ------------------------------------------------------------------

@dataclass
class ScoreModifier:
    """A single score modifier with reason."""

    delta: int
    reason: str


@dataclass
class ScoreContext:
    """Context for computing catalyst score and actionability."""

    event_type: str
    direction: str
    strength: int
    classifier_confidence: float
    mapping_confidence: float
    source_tier: SourceTier | str | None = None
    has_multiple_sources: bool = False
    is_stale: bool = False
    is_secondary_source: bool = False
    active_public_ticker: bool = False
    market_snapshot_available: bool = False
    intraday_move_pct: float | None = None

    # Additional context
    has_direct_official_confirmation: bool = False
    fetch_path_has_delay: bool = False


# ------------------------------------------------------------------
# Scoring logic
# ------------------------------------------------------------------


def compute_catalyst_score(context: ScoreContext) -> int:
    """Compute the base catalyst score from event type and strength."""
    base = get_base_catalyst_score(context.event_type)
    # The classifier's strength field provides additional granularity
    # but the base score from event type is the floor
    return max(base, context.strength)


def compute_modifiers(context: ScoreContext) -> list[ScoreModifier]:
    """Compute all applicable modifiers for a given context.

    Returns a list of ScoreModifier with delta and reason.
    """
    modifiers: list[ScoreModifier] = []

    # +1: Multiple independent official sources
    if context.has_multiple_sources:
        modifiers.append(ScoreModifier(delta=1, reason="Multiple independent sources"))

    # +1: Direct official source adds concrete action/amount
    if context.has_direct_official_confirmation:
        modifiers.append(
            ScoreModifier(delta=1, reason="Official confirmation with concrete details")
        )

    # -1: Mapping confidence below 0.90
    if context.mapping_confidence < 0.90:
        modifiers.append(
            ScoreModifier(delta=-1, reason="Mapping confidence below 0.90")
        )

    # -1: Stale or recycled event
    if context.is_stale:
        modifiers.append(ScoreModifier(delta=-1, reason="Stale or recycled event"))

    # -2: Source is secondary-only or fetch path has material delay (spec §11)
    if context.is_secondary_source or context.fetch_path_has_delay:
        if context.is_secondary_source and context.fetch_path_has_delay:
            reason = "Secondary source with delayed fetch path"
        elif context.is_secondary_source:
            reason = "Secondary source only"
        else:
            reason = "Fetch path has material delay"
        modifiers.append(ScoreModifier(delta=-2, reason=reason))

    # Note: price-move downgrades are handled in _determine_alert_level
    # and apply_market_downgrade, not as score modifiers.
    return modifiers


def compute_actionability(context: ScoreContext) -> SignalDecision:
    """Compute final catalyst score, actionability, and alert level.

    Market data may only downgrade, never promote.
    """
    base_score = compute_catalyst_score(context)
    modifiers = compute_modifiers(context)

    total_modifier = sum(m.delta for m in modifiers)
    final_score = max(0, base_score + total_modifier)

    modifier_reasons = [m.reason for m in modifiers]
    decision_reasons: list[str] = []

    # Determine alert level (price-move downgrades are handled inside
    # _determine_alert_level, symmetric for bullish and bearish events).
    level = _determine_alert_level(context, final_score)

    # Build reasons
    decision_reasons.append(f"Base score: {base_score} from {context.event_type}")
    if modifiers:
        decision_reasons.append(f"Modifiers: {total_modifier:+d}")

    return SignalDecision(
        alert_level=level,
        catalyst_score=final_score,
        direction=Direction(context.direction),
        modifiers=modifier_reasons,
        reasons=decision_reasons,
    )


def _determine_alert_level(
    context: ScoreContext,
    final_score: int,
) -> AlertLevel:
    """Determine the alert level based on context and final score.

    Market data may only downgrade, never promote.
    Price-move thresholds are spec-consistent with
    ``marketdata.downgrade.apply_market_downgrade``:

    * Absolute move below 10%  → retain TRADEABLE (if gate passes)
    * Absolute move 10%–25%    → downgrade to REVIEW
    * Absolute move above 25%  → downgrade to AVOID_CHASE

    Bearish negative moves are evaluated symmetrically by absolute magnitude;
    the signed text is preserved in reasons.
    """
    # Check IGNORE first
    if context.event_type == EventType.IRRELEVANT.value or not context.active_public_ticker:
        return AlertLevel.IGNORE

    # Check TRADEABLE gate
    if _passes_tradeable_gate(context, final_score):
        # Market data may downgrade
        if not context.market_snapshot_available:
            return AlertLevel.REVIEW

        intraday = context.intraday_move_pct
        if intraday is not None:
            abs_move = abs(intraday)
            # Below +10% (absolute): retain TRADEABLE
            if abs_move < 10.0:
                return AlertLevel.TRADEABLE
            # +10% through +25% (absolute): downgrade to REVIEW
            if abs_move <= 25.0:
                return AlertLevel.REVIEW
            # Above +25% (absolute): downgrade to AVOID_CHASE
            return AlertLevel.AVOID_CHASE

        return AlertLevel.TRADEABLE

    # Check WATCH: useful internal event
    if final_score >= 2 and context.source_tier == SourceTier.TIER_1.value:
        return AlertLevel.WATCH

    # Default: REVIEW
    if final_score >= 1:
        return AlertLevel.REVIEW

    return AlertLevel.IGNORE


def _passes_tradeable_gate(context: ScoreContext, final_score: int) -> bool:
    """Check if all TRADEABLE conditions are met.

    All conditions must be true:
    - Validated active public ticker.
    - Mapping confidence at least 0.90.
    - Classifier confidence at least 0.80.
    - Source is Tier 1 (direct official source) or Truth Social.
    - Direction is bullish or bearish.
    - Catalyst score at least 5.
    - Market snapshot is available.
    - Event is not stale or recycled.
    """
    if not context.active_public_ticker:
        return False
    if context.mapping_confidence < 0.90:
        return False
    if context.classifier_confidence < 0.80:
        return False
    if context.source_tier not in (SourceTier.TIER_1.value, "tier_1"):
        return False
    if context.direction not in (Direction.BULLISH.value, Direction.BEARISH.value):
        return False
    if final_score < 5:
        return False
    if context.is_stale:
        return False
    # Market snapshot availability is checked in _determine_alert_level
    # (it downgrades but doesn't block the gate check)
    return True
