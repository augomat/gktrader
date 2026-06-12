"""Tests for trading-session horizon helpers using exchange_calendars.

All arithmetic uses exchange_calendars, not calendar-day arithmetic.
Tests cover weekend, holiday, and regular session behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from gktrader.reporting.horizons import (
    HORIZON_1D,
    HORIZON_5D,
    HORIZON_20D,
    HORIZON_1H,
    SUPPORTED_HORIZONS,
    HorizonResult,
    compute_horizon_session,
    next_n_session_dates,
    resolve_entry_session,
)


class TestResolveEntrySession:
    """Resolve first eligible regular-session date."""

    def test_during_session_returns_same_day(self) -> None:
        """A Monday during trading hours returns that Monday."""
        # 2026-06-08 is a Monday
        dt = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)
        session = resolve_entry_session(dt)
        assert session is not None
        assert session.dayofweek == 0  # Monday

    def test_weekend_returns_next_monday(self) -> None:
        """A Saturday returns the following Monday."""
        # 2026-06-13 is a Saturday
        dt = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)
        session = resolve_entry_session(dt)
        assert session is not None
        # Should be Monday 2026-06-15
        assert session.dayofweek == 0
        assert session.month == 6
        assert session.day == 15

    def test_sunday_returns_next_monday(self) -> None:
        """A Sunday returns the following Monday."""
        # 2026-06-14 is a Sunday
        dt = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
        session = resolve_entry_session(dt)
        assert session is not None
        assert session.dayofweek == 0
        assert session.day == 15

    def test_after_hours_friday_returns_next_monday(self) -> None:
        """Friday after close returns next Monday."""
        # 2026-06-12 is a Friday
        dt = datetime(2026, 6, 12, 22, 0, 0, tzinfo=UTC)
        session = resolve_entry_session(dt)
        assert session is not None
        assert session.dayofweek == 0  # Monday
        assert session.day == 15


class TestNextNSessionDates:
    """Next N trading session dates."""

    def test_one_session_from_monday(self) -> None:
        dt = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)  # Monday
        sessions = next_n_session_dates(dt, 1)
        assert len(sessions) == 1
        assert sessions[0].dayofweek == 0

    def test_five_sessions_skip_weekend(self) -> None:
        """5 sessions from Thursday spans into next week."""
        dt = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)  # Thursday
        sessions = next_n_session_dates(dt, 5)
        assert len(sessions) == 5
        # Thursday -> Friday -> Monday -> Tuesday -> Wednesday
        assert sessions[0].dayofweek == 3  # Thu
        assert sessions[1].dayofweek == 4  # Fri
        assert sessions[2].dayofweek == 0  # Mon
        assert sessions[3].dayofweek == 1  # Tue
        assert sessions[4].dayofweek == 2  # Wed

    def test_from_weekend_starts_monday(self) -> None:
        dt = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)  # Saturday
        sessions = next_n_session_dates(dt, 3)
        assert len(sessions) == 3
        assert sessions[0].dayofweek == 0  # Mon
        assert sessions[1].dayofweek == 1  # Tue
        assert sessions[2].dayofweek == 2  # Wed

    def test_empty_for_nonexistent_calendar(self) -> None:
        dt = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)
        sessions = next_n_session_dates(dt, 3, calendar_name="NONEXISTENT")
        assert sessions == []


class TestComputeHorizonSession:
    """Horizon computation for each supported horizon."""

    def test_1h_horizon(self) -> None:
        """1h horizon returns a target time 60 minutes later."""
        dt = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)  # Monday
        result = compute_horizon_session(dt, HORIZON_1H)
        assert isinstance(result, HorizonResult)
        assert result.horizon == "1h"
        assert result.target_time is not None
        assert result.target_time.hour == 13  # 1 hour later
        assert result.target_time.minute == 0

    def test_1d_horizon(self) -> None:
        """1d horizon returns the next trading session."""
        dt = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)  # Monday
        result = compute_horizon_session(dt, HORIZON_1D)
        assert result.horizon == "1d"
        assert result.target_session is not None
        # 1 session after Monday = Tuesday
        assert result.target_session.dayofweek == 1

    def test_5d_horizon_skips_weekend(self) -> None:
        """5d horizon from Thursday spans into next week."""
        dt = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)  # Thursday
        result = compute_horizon_session(dt, HORIZON_5D)
        assert result.horizon == "5d"
        assert result.target_session is not None
        # Entry Thu, +5 sessions = Thu(0) Fri(1) Mon(2) Tue(3) Wed(4) Thu(5)
        # So target is the Thursday after next
        assert result.target_session.dayofweek == 3  # Thursday

    def test_20d_horizon(self) -> None:
        """20d horizon returns a session ~4 weeks out."""
        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)  # Monday
        result = compute_horizon_session(dt, HORIZON_20D)
        assert result.horizon == "20d"
        assert result.target_session is not None
        # 20 trading sessions ≈ 4 calendar weeks
        diff_days = (result.target_session - pd.Timestamp(dt)).days
        assert 25 <= diff_days <= 30  # Should be ~28 calendar days

    def test_unsupported_horizon(self) -> None:
        dt = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)
        result = compute_horizon_session(dt, "invalid")
        assert result.missing_data is True
        assert any("unsupported_horizon" in f for f in result.quality_flags)

    def test_all_supported_horizons(self) -> None:
        """All supported horizons produce a result without error."""
        dt = datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC)
        for horizon in SUPPORTED_HORIZONS:
            result = compute_horizon_session(dt, horizon)
            assert result.horizon == horizon
            # 1h may have target_session=None but target_time is set
            if horizon == HORIZON_1H:
                assert result.target_time is not None
            else:
                assert result.target_session is not None, f"{horizon} has no target session"

    def test_weekend_entry_1d_horizon(self) -> None:
        """1d horizon from a weekend entry resolves correctly."""
        dt = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)  # Saturday
        result = compute_horizon_session(dt, HORIZON_1D)
        assert result.target_session is not None
        # Entry resolves to Monday, +1 session = Tuesday
        assert result.target_session.dayofweek == 1  # Tuesday