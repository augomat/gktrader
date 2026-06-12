"""Health transition notifications for source degradation and recovery.

Rules:
- Mark a source degraded after three consecutive failures.
- Mark a source critical after ten minutes without a successful poll.
- Send one Telegram message on degradation transition and one on recovery.
- Do not send repeated failure spam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum, auto


class HealthTransition(StrEnum):
    HEALTHY_TO_DEGRADED = auto()
    DEGRADED_TO_CRITICAL = auto()
    CRITICAL_TO_DEGRADED = auto()
    DEGRADED_TO_HEALTHY = auto()
    CRITICAL_TO_HEALTHY = auto()


_HEALTH_HEADERS: dict[HealthTransition, tuple[str, str]] = {
    HealthTransition.HEALTHY_TO_DEGRADED: (
        "⚠️",
        "Source Degraded",
    ),
    HealthTransition.DEGRADED_TO_CRITICAL: (
        "🚨",
        "Source Critical",
    ),
    HealthTransition.CRITICAL_TO_DEGRADED: (
        "⚠️",
        "Source Improving (was Critical)",
    ),
    HealthTransition.DEGRADED_TO_HEALTHY: (
        "✅",
        "Source Recovered",
    ),
    HealthTransition.CRITICAL_TO_HEALTHY: (
        "✅",
        "Source Recovered (was Critical)",
    ),
}


@dataclass
class SourceHealthState:
    """Tracks the health state of a single source."""

    source_name: str
    consecutive_failures: int = 0
    last_successful_poll: datetime | None = None
    is_degraded: bool = False
    is_critical: bool = False


def compute_health_transition(
    old_state: SourceHealthState,
    new_state: SourceHealthState,
) -> HealthTransition | None:
    """Compute the health transition between two source health states.

    Args:
        old_state: The previous health state.
        new_state: The current health state.

    Returns:
        The HealthTransition if a notifiable change occurred, or None.
    """
    was_healthy = not old_state.is_degraded and not old_state.is_critical
    was_degraded = old_state.is_degraded and not old_state.is_critical
    was_critical = old_state.is_critical

    is_healthy = not new_state.is_degraded and not new_state.is_critical
    is_degraded = new_state.is_degraded and not new_state.is_critical
    is_critical = new_state.is_critical

    if was_healthy and is_degraded:
        return HealthTransition.HEALTHY_TO_DEGRADED
    if was_healthy and is_critical:
        return HealthTransition.HEALTHY_TO_DEGRADED  # skip straight to degraded
    if was_degraded and is_critical:
        return HealthTransition.DEGRADED_TO_CRITICAL
    if was_critical and is_degraded:
        return HealthTransition.CRITICAL_TO_DEGRADED
    if (was_degraded or was_critical) and is_healthy:
        return (
            HealthTransition.CRITICAL_TO_HEALTHY
            if was_critical
            else HealthTransition.DEGRADED_TO_HEALTHY
        )

    return None


def evaluate_source_health(
    state: SourceHealthState,
    consecutive_failures: int,
    last_successful_poll: datetime | None,
    failure_threshold: int = 3,
    critical_minutes: int = 10,
    now: datetime | None = None,
) -> SourceHealthState:
    """Evaluate the current health of a source based on poll results.

    Args:
        state: The current source health state.
        consecutive_failures: Current count of consecutive poll failures.
        last_successful_poll: Timestamp of the last successful poll.
        failure_threshold: Number of consecutive failures to mark degraded.
        critical_minutes: Minutes without success to mark critical.
        now: Current UTC time (defaults to UTC now).

    Returns:
        An updated SourceHealthState.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    is_degraded = consecutive_failures >= failure_threshold
    is_critical = False

    if is_degraded and last_successful_poll is not None:
        elapsed = (now - last_successful_poll).total_seconds() / 60
        if elapsed >= critical_minutes:
            is_critical = True

    return SourceHealthState(
        source_name=state.source_name,
        consecutive_failures=consecutive_failures,
        last_successful_poll=last_successful_poll,
        is_degraded=is_degraded or is_critical,
        is_critical=is_critical,
    )


def build_degradation_notification(
    source_name: str,
    transition: HealthTransition,
    consecutive_failures: int,
    last_successful_poll: datetime | None = None,
) -> str:
    """Build a health degradation notification message.

    Args:
        source_name: The name of the affected source.
        transition: The health transition that occurred.
        consecutive_failures: Consecutive failure count.
        last_successful_poll: Timestamp of the last successful poll.

    Returns:
        A formatted notification message string.
    """
    emoji, header_text = _HEALTH_HEADERS.get(
        transition,
        ("⚠️", "Source Health Changed"),
    )

    lines: list[str] = [
        f"{emoji} {header_text}: {source_name}",
        f"  Consecutive failures: {consecutive_failures}",
    ]

    if last_successful_poll:
        last_str = last_successful_poll.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"  Last successful poll: {last_str}")

    if transition == HealthTransition.DEGRADED_TO_CRITICAL:
        lines.append("  ⚠ Source has been failing for an extended period.")

    return "\n".join(lines)


def build_recovery_notification(
    source_name: str,
    transition: HealthTransition,
) -> str:
    """Build a health recovery notification message.

    Args:
        source_name: The name of the recovered source.
        transition: The health transition that occurred.

    Returns:
        A formatted notification message string.
    """
    emoji, header_text = _HEALTH_HEADERS.get(
        transition,
        ("✅", "Source Recovered"),
    )

    return f"{emoji} {header_text}: {source_name}"