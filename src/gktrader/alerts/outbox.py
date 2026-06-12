"""Transactional outbox helpers for alert delivery.

Provides safe claim, success, unknown, and failure tracking for the
transactional outbox pattern.

Favors at-most-once delivery:
- If a Telegram request times out after dispatch, mark delivery as UNKNOWN
  and do not blindly resend.
- Claim ensures only one worker processes a given outbox row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum, auto
from typing import Any

from gktrader.domain.enums import DeliveryStatus


class OutboxClaimResult(StrEnum):
    CLAIMED = auto()
    ALREADY_CLAIMED = auto()
    ALREADY_SENT = auto()
    NOT_FOUND = auto()


@dataclass
class OutboxEntry:
    """Represents an alert outbox entry for processing."""

    id: str
    alert_id: str
    status: DeliveryStatus
    idempotency_key: str
    claimed_at: datetime | None = None
    sent_at: datetime | None = None
    message_id: str | None = None


def generate_idempotency_key(alert_id: str, attempt: int = 0) -> str:
    """Generate a deterministic idempotency key for a delivery attempt.

    Args:
        alert_id: The alert UUID.
        attempt: Attempt number (0 for first attempt).

    Returns:
        A deterministic idempotency key string.
    """
    return f"deliver:{alert_id}:v{attempt}"


def claim_outbox(
    entry: OutboxEntry | None,
    now: datetime | None = None,
    stale_claim_seconds: int = 300,
) -> OutboxClaimResult:
    """Attempt to claim an outbox entry for processing.

    This is a stateless check that mirrors what a database query would do.
    For actual DB-backed claims, use a SELECT ... FOR UPDATE SKIP LOCKED pattern.

    Args:
        entry: The outbox entry to claim, or None if not found.
        now: Current UTC datetime.
        stale_claim_seconds: Seconds after which a CLAIMED entry is considered
            stale and can be reclaimed.

    Returns:
        OutboxClaimResult indicating the outcome.
    """
    if entry is None:
        return OutboxClaimResult.NOT_FOUND

    if entry.status == DeliveryStatus.SENT:
        return OutboxClaimResult.ALREADY_SENT

    if entry.status == DeliveryStatus.PENDING:
        return OutboxClaimResult.CLAIMED

    if entry.status == DeliveryStatus.CLAIMED:
        # Check for stale claims
        if now is None:
            now = datetime.now(timezone.utc)
        if (
            entry.claimed_at is not None
            and (now - entry.claimed_at).total_seconds() > stale_claim_seconds
        ):
            return OutboxClaimResult.CLAIMED
        return OutboxClaimResult.ALREADY_CLAIMED

    if entry.status == DeliveryStatus.UNKNOWN:
        # UNKNOWN means we dispatched but don't know the result.
        # Do not blindly resend — favor at-most-once.
        return OutboxClaimResult.ALREADY_CLAIMED

    if entry.status == DeliveryStatus.FAILED:
        # Failed entries can be retried
        return OutboxClaimResult.CLAIMED

    return OutboxClaimResult.ALREADY_CLAIMED


def mark_delivery_outcome(
    entry: OutboxEntry,
    status: DeliveryStatus,
    message_id: str | None = None,
    now: datetime | None = None,
) -> OutboxEntry:
    """Record the outcome of a delivery attempt on an outbox entry.

    Args:
        entry: The outbox entry to update.
        status: The final delivery status (SENT, UNKNOWN, or FAILED).
        message_id: Telegram message ID if the send succeeded.
        now: Current UTC datetime.

    Returns:
        A new OutboxEntry with updated fields.

    Raises:
        ValueError: If status is not a terminal state (SENT, UNKNOWN, FAILED).
    """
    if status not in (DeliveryStatus.SENT, DeliveryStatus.UNKNOWN, DeliveryStatus.FAILED):
        raise ValueError(
            f"mark_delivery_outcome requires a terminal status, got {status}"
        )

    if now is None:
        now = datetime.now(timezone.utc)

    entry.status = status
    entry.sent_at = now

    return entry
