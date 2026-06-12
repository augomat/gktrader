"""Cooldown and material-update logic for event deduplication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# Default cooldown duration: 6 hours
DEFAULT_COOLDOWN_HOURS = 6


@dataclass(frozen=True)
class CooldownKey:
    """Key for cooldown tracking per (ticker, event_type, direction)."""

    ticker: str
    event_type: str
    direction: str

    def __str__(self) -> str:
        return f"{self.ticker.upper()}:{self.event_type}:{self.direction}"


@dataclass
class CooldownState:
    """State of a cooldown for a given key."""

    key: CooldownKey
    last_alerted_at: datetime
    fingerprint: str | None = None
    is_on_cooldown: bool = True

    @property
    def expires_at(self) -> datetime:
        """Return when the cooldown expires."""
        return self.last_alerted_at + timedelta(hours=DEFAULT_COOLDOWN_HOURS)

    @property
    def remaining_seconds(self) -> float:
        """Return seconds remaining in the cooldown (0 if expired)."""
        remaining = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, remaining)


@dataclass
class MaterialUpdateCheck:
    """Result of checking whether a new event is a material update."""

    is_material: bool
    reasons: list[str] = None

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = []


def is_on_cooldown(
    state: CooldownState | None,
    now: datetime | None = None,
) -> bool:
    """Check if a cooldown is still active.

    Args:
        state: The current cooldown state, or None if no prior event.
        now: Current datetime (defaults to UTC now).

    Returns:
        True if the event is on cooldown, False otherwise.
    """
    if state is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    return now < state.expires_at


def is_material_update(
    previous_event: dict[str, Any],
    new_event: dict[str, Any],
) -> MaterialUpdateCheck:
    """Check whether a new event represents a material update that
    overrides the cooldown.

    A materially new event overrides the cooldown when at least one is true:
    - Direction changes.
    - Action status changes (e.g. proposed -> awarded -> cancelled).
    - A new official source confirms the event.
    - A new amount, award ID, or contract ID appears.
    - Catalyst score or alert level increases.
    - A revised source adds materially different evidence.

    Args:
        previous_event: Dict with fields from the prior canonical event.
        new_event: Dict with fields from the new candidate event.

    Returns:
        MaterialUpdateCheck with result and reasons.
    """
    reasons: list[str] = []
    is_material = False

    # Direction change
    prev_dir = previous_event.get("direction", "")
    new_dir = new_event.get("direction", "")
    if prev_dir and new_dir and prev_dir != new_dir:
        reasons.append(f"Direction changed: {prev_dir} -> {new_dir}")
        is_material = True

    # Action status change
    prev_status = previous_event.get("action_status", "")
    new_status = new_event.get("action_status", "")
    if prev_status and new_status and prev_status != new_status:
        reasons.append(f"Action status changed: {prev_status} -> {new_status}")
        is_material = True

    # New source confirmation
    prev_sources = set(previous_event.get("source_names", []) or [])
    new_sources = set(new_event.get("source_names", []) or [])
    if new_sources - prev_sources:
        added = new_sources - prev_sources
        reasons.append(f"New source(s): {', '.join(sorted(added))}")
        is_material = True

    # New award/contract IDs
    prev_awards = set(previous_event.get("award_or_contract_ids", []) or [])
    new_awards = set(new_event.get("award_or_contract_ids", []) or [])
    if new_awards - prev_awards:
        reasons.append("New award/contract IDs appeared")
        is_material = True

    # New monetary amounts
    prev_amounts = set(previous_event.get("monetary_amounts", []) or [])
    new_amounts = set(new_event.get("monetary_amounts", []) or [])
    if new_amounts - prev_amounts:
        reasons.append("New monetary amounts appeared")
        is_material = True

    # Score increase
    prev_score = previous_event.get("catalyst_score", 0) or 0
    new_score = new_event.get("catalyst_score", 0) or 0
    if new_score > prev_score:
        reasons.append(f"Catalyst score increased: {prev_score} -> {new_score}")
        is_material = True

    # Alert level increase
    prev_level = previous_event.get("alert_level", "")
    new_level = new_event.get("alert_level", "")
    if prev_level and new_level and _level_rank(new_level) > _level_rank(prev_level):
        reasons.append(f"Alert level increased: {prev_level} -> {new_level}")
        is_material = True

    return MaterialUpdateCheck(is_material=is_material, reasons=reasons)


def _level_rank(level: str) -> int:
    """Return numeric rank for alert level comparison."""
    ranks = {
        "IGNORE": 0,
        "WATCH": 1,
        "REVIEW": 2,
        # AVOID_CHASE is a price-driven downgrade from TRADEABLE, not a promotion.
        # Ranking it below TRADEABLE prevents a TRADEABLE→AVOID_CHASE transition
        # from being treated as a level "increase" and wrongly triggering a material update.
        "AVOID_CHASE": 2,
        "TRADEABLE": 3,
    }
    return ranks.get(level, 0)