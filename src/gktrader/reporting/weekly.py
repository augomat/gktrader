"""Weekly performance report generation.

Weekly reports group performance by source, event type, direction, and
alert level.  All IEX-derived results are clearly labelled as partial-market
measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from gktrader.domain.enums import AlertLevel, Direction, EventType

IEX_PARTIAL_LABEL = "IEX partial-market data"


@dataclass
class WeeklyReportRow:
    """A single row in a weekly performance report."""

    source_name: str
    event_type: str
    direction: Direction | str
    alert_level: AlertLevel | str
    ticker: str
    notional_eur: float
    return_pct: float | None = None
    max_drawdown_pct: float | None = None
    max_runup_pct: float | None = None
    missing_data: bool = False
    label: str = IEX_PARTIAL_LABEL


@dataclass
class GroupedSection:
    """A grouped section of the weekly report."""

    group_key: tuple[str, str, str, str]  # (source, event_type, direction, alert_level)
    rows: list[WeeklyReportRow] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def total_notional(self) -> float:
        return sum(r.notional_eur for r in self.rows)

    @property
    def avg_return_pct(self) -> float | None:
        returns = [r.return_pct for r in self.rows if r.return_pct is not None]
        if not returns:
            return None
        return sum(returns) / len(returns)


def group_performance_rows(
    rows: list[WeeklyReportRow],
) -> list[GroupedSection]:
    """Group *rows* by (source, event_type, direction, alert_level).

    Returns a list of ``GroupedSection`` sorted by group key.
    """
    groups: dict[tuple[str, str, str, str], list[WeeklyReportRow]] = {}
    for row in rows:
        key = (row.source_name, row.event_type, str(row.direction), str(row.alert_level))
        groups.setdefault(key, []).append(row)

    return [
        GroupedSection(group_key=k, rows=v)
        for k, v in sorted(groups.items())
    ]


def build_weekly_report(
    rows: list[WeeklyReportRow],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a weekly report payload from performance *rows*.

    Returns a dict suitable for storage in ``WeeklyReport.report_payload``
    and for rendering into a Telegram message.
    """
    if generated_at is None:
        generated_at = datetime.now(UTC)

    sections = group_performance_rows(rows)

    summary_lines: list[str] = [
        f"📊 Weekly Performance Report",
        f"Generated: {generated_at.isoformat()}",
        f"Data source: {IEX_PARTIAL_LABEL}",
        "",
    ]

    total_trades = len(rows)
    total_notional = sum(r.notional_eur for r in rows)
    summary_lines.append(f"Total paper trades: {total_trades}")
    summary_lines.append(f"Total notional: EUR {total_notional:,.2f}")
    summary_lines.append("")

    for section in sections:
        src, evt, direction, level = section.group_key
        header = f"  [{level}] {direction.upper()} | {evt} | {src}"
        summary_lines.append(header)
        for row in section.rows:
            ret_str = f"{row.return_pct:+.2f}%" if row.return_pct is not None else "N/A"
            dd_str = f"{row.max_drawdown_pct:.2f}%" if row.max_drawdown_pct is not None else "N/A"
            ru_str = f"{row.max_runup_pct:.2f}%" if row.max_runup_pct is not None else "N/A"
            missing = " [MISSING DATA]" if row.missing_data else ""
            summary_lines.append(
                f"    {row.ticker}: EUR {row.notional_eur:,.0f} | "
                f"Return: {ret_str} | DD: {dd_str} | Runup: {ru_str}{missing}"
            )
        summary_lines.append("")

    summary_lines.append(f"--- End of report ---")

    return {
        "generated_at": generated_at.isoformat(),
        "label": IEX_PARTIAL_LABEL,
        "summary": "\n".join(summary_lines),
        "total_trades": total_trades,
        "total_notional_eur": total_notional,
        "sections": [
            {
                "group_key": list(s.group_key),
                "count": s.count,
                "total_notional_eur": s.total_notional,
                "avg_return_pct": s.avg_return_pct,
                "rows": [
                    {
                        "source_name": r.source_name,
                        "event_type": r.event_type,
                        "direction": str(r.direction),
                        "alert_level": str(r.alert_level),
                        "ticker": r.ticker,
                        "notional_eur": r.notional_eur,
                        "return_pct": r.return_pct,
                        "max_drawdown_pct": r.max_drawdown_pct,
                        "max_runup_pct": r.max_runup_pct,
                        "missing_data": r.missing_data,
                        "label": r.label,
                    }
                    for r in s.rows
                ],
            }
            for s in sections
        ],
    }