"""Bearish-history query and helper utilities.

For every delivered bearish alert, query prior bullish canonical signals
for the same validated company and return structured summaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gktrader.domain.contracts import PriorBullishSignal


@dataclass
class BullishHistoryResult:
    """Result of querying prior bullish signals for a bearish alert."""

    ticker: str
    company_name: str
    prior_signals: list[PriorBullishSignal] = field(default_factory=list)
    truncated: bool = False
    total_count: int = 0


def collect_bullish_history(
    ticker: str,
    company_name: str,
    prior_signals: list[PriorBullishSignal],
    max_signals: int = 50,
) -> BullishHistoryResult:
    """Collect and format prior bullish signals for a bearish alert context.

    Args:
        ticker: The validated ticker symbol.
        company_name: The validated company name.
        prior_signals: List of prior bullish PriorBullishSignal objects.
        max_signals: Maximum number of signals to include (default 50).

    Returns:
        BullishHistoryResult with signals, truncation status, and counts.
    """
    total = len(prior_signals)
    truncated = total > max_signals

    included = sorted(
        prior_signals,
        key=lambda s: s.source_date,
        reverse=True,
    )[:max_signals]

    return BullishHistoryResult(
        ticker=ticker,
        company_name=company_name,
        prior_signals=included,
        truncated=truncated,
        total_count=total,
    )


def format_bullish_history_for_alert(history: BullishHistoryResult) -> str:
    """Format bullish history for inclusion in a bearish alert message.

    Args:
        history: The BullishHistoryResult to format.

    Returns:
        A formatted string block ready for inclusion in an alert message.
    """
    if not history.prior_signals:
        return "No prior bullish signals found."

    lines: list[str] = [
        f"📈 Prior bullish signals for {history.company_name} ({history.ticker}):"
    ]

    for signal in history.prior_signals:
        date_str = signal.source_date.strftime("%Y-%m-%d")
        lines.append(
            f"  • {date_str} | {signal.event_type} | {signal.alert_level} | "
            f"{signal.rationale}"
        )

    if history.truncated:
        remaining = history.total_count - len(history.prior_signals)
        lines.append(
            f"\n  ⚠ {remaining} older signal(s) not shown (max {len(history.prior_signals)} displayed)."
        )

    return "\n".join(lines)