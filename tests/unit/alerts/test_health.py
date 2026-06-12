"""Tests for health transition notifications.

Covers degradation, critical, and recovery transitions,
and notification message formatting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gktrader.alerts.health import (
    HealthTransition,
    SourceHealthState,
    build_degradation_notification,
    build_recovery_notification,
    compute_health_transition,
    evaluate_source_health,
)


class TestEvaluateSourceHealth:
    """Source health evaluation."""

    def test_healthy_state(self) -> None:
        state = SourceHealthState(source_name="test_source")
        now = datetime.now(timezone.utc)
        result = evaluate_source_health(
            state=state,
            consecutive_failures=0,
            last_successful_poll=now,
            now=now,
        )
        assert not result.is_degraded
        assert not result.is_critical

    def test_three_failures_becomes_degraded(self) -> None:
        state = SourceHealthState(source_name="test_source")
        now = datetime.now(timezone.utc)
        result = evaluate_source_health(
            state=state,
            consecutive_failures=3,
            last_successful_poll=now - timedelta(minutes=1),
            failure_threshold=3,
            now=now,
        )
        assert result.is_degraded
        assert not result.is_critical

    def test_two_failures_not_degraded(self) -> None:
        state = SourceHealthState(source_name="test_source")
        now = datetime.now(timezone.utc)
        result = evaluate_source_health(
            state=state,
            consecutive_failures=2,
            last_successful_poll=now,
            failure_threshold=3,
            now=now,
        )
        assert not result.is_degraded

    def test_degraded_becomes_critical_after_10_min(self) -> None:
        state = SourceHealthState(source_name="test_source")
        now = datetime.now(timezone.utc)
        result = evaluate_source_health(
            state=state,
            consecutive_failures=5,
            last_successful_poll=now - timedelta(minutes=15),
            failure_threshold=3,
            critical_minutes=10,
            now=now,
        )
        assert result.is_degraded
        assert result.is_critical

    def test_degraded_but_not_yet_critical(self) -> None:
        state = SourceHealthState(source_name="test_source")
        now = datetime.now(timezone.utc)
        result = evaluate_source_health(
            state=state,
            consecutive_failures=3,
            last_successful_poll=now - timedelta(minutes=5),
            failure_threshold=3,
            critical_minutes=10,
            now=now,
        )
        assert result.is_degraded
        assert not result.is_critical

    def test_recovery_resets_health(self) -> None:
        state = SourceHealthState(
            source_name="test_source",
            is_degraded=True,
            is_critical=False,
            consecutive_failures=3,
        )
        now = datetime.now(timezone.utc)
        # Successful poll resets failures to 0
        result = evaluate_source_health(
            state=state,
            consecutive_failures=0,
            last_successful_poll=now,
            now=now,
        )
        assert not result.is_degraded
        assert not result.is_critical


class TestComputeHealthTransition:
    """Health transition detection."""

    def test_healthy_to_degraded(self) -> None:
        old = SourceHealthState(source_name="test", is_degraded=False, is_critical=False)
        new = SourceHealthState(source_name="test", is_degraded=True, is_critical=False)
        transition = compute_health_transition(old, new)
        assert transition == HealthTransition.HEALTHY_TO_DEGRADED

    def test_degraded_to_critical(self) -> None:
        old = SourceHealthState(source_name="test", is_degraded=True, is_critical=False)
        new = SourceHealthState(source_name="test", is_degraded=True, is_critical=True)
        transition = compute_health_transition(old, new)
        assert transition == HealthTransition.DEGRADED_TO_CRITICAL

    def test_critical_to_degraded(self) -> None:
        old = SourceHealthState(source_name="test", is_degraded=True, is_critical=True)
        new = SourceHealthState(source_name="test", is_degraded=True, is_critical=False)
        transition = compute_health_transition(old, new)
        assert transition == HealthTransition.CRITICAL_TO_DEGRADED

    def test_degraded_to_healthy(self) -> None:
        old = SourceHealthState(source_name="test", is_degraded=True, is_critical=False)
        new = SourceHealthState(source_name="test", is_degraded=False, is_critical=False)
        transition = compute_health_transition(old, new)
        assert transition == HealthTransition.DEGRADED_TO_HEALTHY

    def test_critical_to_healthy(self) -> None:
        old = SourceHealthState(source_name="test", is_degraded=True, is_critical=True)
        new = SourceHealthState(source_name="test", is_degraded=False, is_critical=False)
        transition = compute_health_transition(old, new)
        assert transition == HealthTransition.CRITICAL_TO_HEALTHY

    def test_no_change_returns_none(self) -> None:
        old = SourceHealthState(source_name="test", is_degraded=False, is_critical=False)
        new = SourceHealthState(source_name="test", is_degraded=False, is_critical=False)
        transition = compute_health_transition(old, new)
        assert transition is None

    def test_stay_degraded_no_notification(self) -> None:
        old = SourceHealthState(source_name="test", is_degraded=True, is_critical=False)
        new = SourceHealthState(source_name="test", is_degraded=True, is_critical=False)
        transition = compute_health_transition(old, new)
        assert transition is None

    def test_healthy_skip_straight_to_critical(self) -> None:
        """If a source goes straight from healthy to critical, report degraded first."""
        old = SourceHealthState(source_name="test", is_degraded=False, is_critical=False)
        new = SourceHealthState(source_name="test", is_degraded=True, is_critical=True)
        transition = compute_health_transition(old, new)
        # Goes to degraded first (no direct healthy->critical transition)
        assert transition == HealthTransition.HEALTHY_TO_DEGRADED


class TestBuildDegradationNotification:
    """Degradation notification formatting."""

    def test_degraded_message(self) -> None:
        msg = build_degradation_notification(
            source_name="whitehouse",
            transition=HealthTransition.HEALTHY_TO_DEGRADED,
            consecutive_failures=3,
        )
        assert "Degraded" in msg or "degraded" in msg
        assert "whitehouse" in msg
        assert "3" in msg

    def test_critical_message(self) -> None:
        msg = build_degradation_notification(
            source_name="sec",
            transition=HealthTransition.DEGRADED_TO_CRITICAL,
            consecutive_failures=10,
            last_successful_poll=datetime(2025, 6, 12, 10, 0, tzinfo=timezone.utc),
        )
        assert "Critical" in msg or "critical" in msg
        assert "sec" in msg
        assert "10" in msg
        assert "2025-06-12" in msg


class TestBuildRecoveryNotification:
    """Recovery notification formatting."""

    def test_recovery_message(self) -> None:
        msg = build_recovery_notification(
            source_name="whitehouse",
            transition=HealthTransition.DEGRADED_TO_HEALTHY,
        )
        assert "Recovered" in msg
        assert "whitehouse" in msg

    def test_critical_recovery_message(self) -> None:
        msg = build_recovery_notification(
            source_name="sec",
            transition=HealthTransition.CRITICAL_TO_HEALTHY,
        )
        assert "Recovered" in msg
        assert "sec" in msg