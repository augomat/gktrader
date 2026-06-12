"""Trading-session horizon helpers using exchange_calendars.

Performance horizons:
    - 1h:  first eligible bar at or after 60 minutes.
    - 1d:  close after 1 US trading session.
    - 5d:  close after 5 US trading sessions.
    - 20d: close after 20 US trading sessions.

All arithmetic uses ``exchange_calendars``, not calendar-day arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import exchange_calendars as ec
import pandas as pd

# Default US equity calendar
_XNYS = "XNYS"

# Supported horizon labels
HORIZON_1H = "1h"
HORIZON_1D = "1d"
HORIZON_5D = "5d"
HORIZON_20D = "20d"

SUPPORTED_HORIZONS = (HORIZON_1H, HORIZON_1D, HORIZON_5D, HORIZON_20D)


@dataclass
class HorizonResult:
    """Result of a horizon computation."""

    horizon: str
    target_session: pd.Timestamp | None
    target_time: datetime | None
    missing_data: bool = False
    quality_flags: list[str] = field(default_factory=list)


def _get_calendar(calendar_name: str = _XNYS) -> Any:
    """Get an exchange calendar by name (cached by the library)."""
    try:
        return ec.get_calendar(calendar_name)
    except Exception:
        return None


def _to_utc_timestamp(value: datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _to_session_label(value: datetime | pd.Timestamp) -> pd.Timestamp:
    return _to_utc_timestamp(value).tz_localize(None).normalize()


def _session_output(value: pd.Timestamp | None) -> pd.Timestamp | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.tz_localize("UTC")
    return value.tz_convert("UTC")


def resolve_entry_session(
    alert_time: datetime,
    calendar_name: str = _XNYS,
) -> pd.Timestamp | None:
    """Resolve the first eligible regular-session date for paper entry.

    If *alert_time* falls within a trading session, that session is
    returned.  Otherwise the next trading session is returned.
    Returns ``None`` if no session can be determined.
    """
    cal = _get_calendar(calendar_name)
    if cal is None:
        return None
    alert_ts = _to_utc_timestamp(alert_time)
    session_label = _to_session_label(alert_time)

    if cal.is_session(session_label):
        try:
            session_close = _to_utc_timestamp(cal.session_close(session_label))
            if alert_ts > session_close:
                return _session_output(cal.next_session(session_label))
        except Exception:
            pass
        return _session_output(session_label)

    # Find the next session
    try:
        return _session_output(cal.date_to_session(session_label, direction="next"))
    except Exception:
        return None


def next_n_session_dates(
    from_date: datetime,
    n: int,
    calendar_name: str = _XNYS,
) -> list[pd.Timestamp]:
    """Return the next *n* trading session dates starting from *from_date*.

    If *from_date* is itself a trading session it is included as the
    first element.  Uses ``exchange_calendars`` session arithmetic.
    """
    cal = _get_calendar(calendar_name)
    if cal is None:
        return []
    session_label = _to_session_label(from_date)

    # Normalise to session start
    if not cal.is_session(session_label):
        try:
            session_label = cal.date_to_session(session_label, direction="next")
        except Exception:
            return []
    else:
        try:
            if _to_utc_timestamp(from_date) > _to_utc_timestamp(cal.session_close(session_label)):
                session_label = cal.next_session(session_label)
        except Exception:
            pass

    sessions: list[pd.Timestamp] = [_session_output(session_label)]
    current = session_label
    for _ in range(n - 1):
        try:
            current = cal.next_session(current)
            output = _session_output(current)
            if output is not None:
                sessions.append(output)
        except Exception:
            break
    return sessions


def compute_horizon_session(
    entry_time: datetime,
    horizon: str,
    calendar_name: str = _XNYS,
) -> HorizonResult:
    """Compute the target session for a given performance *horizon*.

    Parameters
    ----------
    entry_time:
        The paper entry timestamp.
    horizon:
        One of ``"1h"``, ``"1d"``, ``"5d"``, ``"20d"``.
    calendar_name:
        Exchange calendar name (default ``XNYS``).

    Returns
    -------
    HorizonResult with the target session and any quality flags.
    """
    cal = _get_calendar(calendar_name)
    if cal is None:
        return HorizonResult(
            horizon=horizon,
            target_session=None,
            target_time=None,
            missing_data=True,
            quality_flags=[f"invalid_calendar: {calendar_name}"],
        )
    quality_flags: list[str] = []

    if horizon == HORIZON_1H:
        # 1h: first eligible bar at or after 60 minutes
        target_time = entry_time + timedelta(hours=1)
        # Find the session containing this time
        ts = _to_session_label(target_time)
        try:
            target_session = _session_output(cal.date_to_session(ts, direction="next"))
        except Exception:
            target_session = None
            quality_flags.append("no_session_for_1h_target")
        return HorizonResult(
            horizon=horizon,
            target_session=target_session,
            target_time=target_time,
            missing_data=target_session is None,
            quality_flags=quality_flags,
        )

    # Session-based horizons: 1d, 5d, 20d
    horizon_map = {
        HORIZON_1D: 1,
        HORIZON_5D: 5,
        HORIZON_20D: 20,
    }
    n_sessions = horizon_map.get(horizon)
    if n_sessions is None:
        return HorizonResult(
            horizon=horizon,
            target_session=None,
            target_time=None,
            missing_data=True,
            quality_flags=[f"unsupported_horizon: {horizon}"],
        )

    sessions = next_n_session_dates(entry_time, n_sessions + 1, calendar_name)
    if len(sessions) <= n_sessions:
        quality_flags.append(f"insufficient_sessions_for_{horizon}")
        return HorizonResult(
            horizon=horizon,
            target_session=None,
            target_time=None,
            missing_data=True,
            quality_flags=quality_flags,
        )

    # The nth session after entry (index n, since index 0 is entry session)
    target_session = sessions[n_sessions]
    # Convert to datetime for the target close time
    try:
        close_time = cal.session_close(_to_session_label(target_session))
        target_time = close_time.to_pydatetime()
    except Exception:
        target_time = target_session.to_pydatetime()

    return HorizonResult(
        horizon=horizon,
        target_session=target_session,
        target_time=target_time,
        missing_data=False,
        quality_flags=quality_flags,
    )
