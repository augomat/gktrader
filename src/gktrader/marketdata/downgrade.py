"""Actionability downgrade helpers based on market data.

Market data may only downgrade an event's actionability, never promote it.
All user-facing output must be labeled ``IEX partial-market data``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gktrader.domain.contracts import MarketSnapshotContract
from gktrader.domain.enums import AlertLevel

IEX_PARTIAL_LABEL = "IEX partial-market data"

# Actionability thresholds (absolute move percentages)
_THRESHOLD_RETAIN = 10.0  # below this: retain if otherwise eligible
_THRESHOLD_REVIEW = 25.0  # +10% through +25%: downgrade to REVIEW
# above +25%: downgrade to AVOID_CHASE


@dataclass
class DowngradeResult:
    """Result of applying market-data downgrade logic."""

    original_level: AlertLevel
    downgraded_level: AlertLevel
    reasons: list[str] = field(default_factory=list)
    label: str = IEX_PARTIAL_LABEL


def apply_market_downgrade(
    current_level: AlertLevel,
    snapshot: MarketSnapshotContract | None,
    *,
    is_bearish: bool = False,
) -> DowngradeResult:
    """Apply market-data downgrade rules to *current_level*.

    Market data may only downgrade, never promote.  If *snapshot* is
    ``None`` or has no price data, a strong event (``TRADEABLE``) is
    downgraded to ``REVIEW``.

    For bullish events the absolute intraday move is evaluated.
    For bearish events the absolute value of the move is used (the
    corresponding negative price move is evaluated symmetrically).

    Returns a ``DowngradeResult`` with the (possibly unchanged) level
    and human-readable reasons.
    """
    reasons: list[str] = []
    label = IEX_PARTIAL_LABEL

    # --- Missing / stale market data -----------------------------------
    if snapshot is None:
        if current_level == AlertLevel.TRADEABLE:
            return DowngradeResult(
                original_level=current_level,
                downgraded_level=AlertLevel.REVIEW,
                reasons=["Missing market data: downgraded from TRADEABLE to REVIEW"],
                label=label,
            )
        return DowngradeResult(
            original_level=current_level,
            downgraded_level=current_level,
            reasons=["No market snapshot available"],
            label=label,
        )

    reasons.append(f"Market context: {label}")

    if snapshot.price is None and snapshot.intraday_move_pct is None:
        if current_level == AlertLevel.TRADEABLE:
            return DowngradeResult(
                original_level=current_level,
                downgraded_level=AlertLevel.REVIEW,
                reasons=reasons + ["Missing market data: downgraded from TRADEABLE to REVIEW"],
                label=label,
            )
        return DowngradeResult(
            original_level=current_level,
            downgraded_level=current_level,
            reasons=reasons,
            label=label,
        )

    # --- Price-move-based downgrades -----------------------------------
    move = snapshot.intraday_move_pct
    if move is None:
        # No intraday move data; cannot apply price-based rules
        if current_level == AlertLevel.TRADEABLE:
            return DowngradeResult(
                original_level=current_level,
                downgraded_level=AlertLevel.REVIEW,
                reasons=reasons + ["No intraday move data: downgraded from TRADEABLE to REVIEW"],
                label=label,
            )
        return DowngradeResult(
            original_level=current_level,
            downgraded_level=current_level,
            reasons=reasons,
            label=label,
        )

    # For bearish events, evaluate the absolute move symmetrically
    abs_move = abs(move)

    reasons.append(f"Intraday move: {move:+.2f}%")

    # Only downgrade if the current level is TRADEABLE (market data cannot
    # promote, so we never upgrade from REVIEW or lower)
    if current_level != AlertLevel.TRADEABLE:
        return DowngradeResult(
            original_level=current_level,
            downgraded_level=current_level,
            reasons=reasons,
            label=label,
        )

    # Below +10%: retain TRADEABLE if every other gate passes
    if abs_move < _THRESHOLD_RETAIN:
        return DowngradeResult(
            original_level=current_level,
            downgraded_level=AlertLevel.TRADEABLE,
            reasons=reasons + [f"Move {move:+.2f}% below {_THRESHOLD_RETAIN:.0f}% threshold: retaining TRADEABLE"],
            label=label,
        )

    # +10% through +25%: downgrade to REVIEW
    if abs_move <= _THRESHOLD_REVIEW:
        return DowngradeResult(
            original_level=current_level,
            downgraded_level=AlertLevel.REVIEW,
            reasons=reasons + [
                f"Move {move:+.2f}% between {_THRESHOLD_RETAIN:.0f}% and {_THRESHOLD_REVIEW:.0f}%: "
                f"downgraded to REVIEW"
            ],
            label=label,
        )

    # Above +25%: downgrade to AVOID_CHASE
    return DowngradeResult(
        original_level=current_level,
        downgraded_level=AlertLevel.AVOID_CHASE,
        reasons=reasons + [
            f"Move {move:+.2f}% above {_THRESHOLD_REVIEW:.0f}%: "
            f"downgraded to AVOID_CHASE"
        ],
        label=label,
    )