"""Paper entry rules, performance horizon calculations, and weekly report generation."""

from gktrader.reporting.horizons import (
    HorizonResult,
    compute_horizon_session,
    next_n_session_dates,
    resolve_entry_session,
)
from gktrader.reporting.paper import (
    PaperEntry,
    get_paper_notional,
    make_paper_entry,
)
from gktrader.reporting.positions import PositionState, apply_position_event
from gktrader.reporting.weekly import (
    WeeklyReportRow,
    build_weekly_report,
    group_performance_rows,
)

__all__ = [
    "HorizonResult",
    "PaperEntry",
    "PositionState",
    "WeeklyReportRow",
    "apply_position_event",
    "build_weekly_report",
    "compute_horizon_session",
    "get_paper_notional",
    "group_performance_rows",
    "make_paper_entry",
    "next_n_session_dates",
    "resolve_entry_session",
]