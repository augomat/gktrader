"""Tests for transactional outbox helpers.

Covers claim behavior, idempotency, unknown/ambiguous delivery handling,
and terminal state tracking.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gktrader.alerts.outbox import (
    OutboxClaimResult,
    OutboxEntry,
    claim_outbox,
    generate_idempotency_key,
    mark_delivery_outcome,
)
from gktrader.domain.enums import DeliveryStatus


class TestGenerateIdempotencyKey:
    """Idempotency key generation."""

    def test_key_format(self) -> None:
        key = generate_idempotency_key("alert-123")
        assert key == "deliver:alert-123:v0"

    def test_key_with_attempt(self) -> None:
        key = generate_idempotency_key("alert-123", attempt=2)
        assert key == "deliver:alert-123:v2"

    def test_different_attempts_different_keys(self) -> None:
        k1 = generate_idempotency_key("alert-123", 0)
        k2 = generate_idempotency_key("alert-123", 1)
        assert k1 != k2


class TestClaimOutbox:
    """Outbox claim behavior."""

    def test_not_found(self) -> None:
        result = claim_outbox(None)
        assert result == OutboxClaimResult.NOT_FOUND

    def test_claim_pending(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.PENDING,
            idempotency_key="key-1",
        )
        result = claim_outbox(entry)
        assert result == OutboxClaimResult.CLAIMED

    def test_already_sent_not_claimable(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.SENT,
            idempotency_key="key-1",
            sent_at=datetime.now(timezone.utc),
        )
        result = claim_outbox(entry)
        assert result == OutboxClaimResult.ALREADY_SENT

    def test_already_claimed_not_claimable(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.CLAIMED,
            idempotency_key="key-1",
            claimed_at=datetime.now(timezone.utc),
        )
        result = claim_outbox(entry)
        assert result == OutboxClaimResult.ALREADY_CLAIMED

    def test_unknown_not_claimable(self) -> None:
        """UNKNOWN means dispatched but result unknown — do not blindly resend."""
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.UNKNOWN,
            idempotency_key="key-1",
            sent_at=datetime.now(timezone.utc),
        )
        result = claim_outbox(entry)
        assert result == OutboxClaimResult.ALREADY_CLAIMED

    def test_failed_is_claimable(self) -> None:
        """FAILED entries can be retried."""
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.FAILED,
            idempotency_key="key-1",
        )
        result = claim_outbox(entry)
        assert result == OutboxClaimResult.CLAIMED

    def test_stale_claim_is_reclaimable(self) -> None:
        """A CLAIMED entry older than stale_claim_seconds can be reclaimed."""
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.CLAIMED,
            idempotency_key="key-1",
            claimed_at=datetime.now(timezone.utc) - timedelta(seconds=600),
        )
        result = claim_outbox(entry, stale_claim_seconds=300)
        assert result == OutboxClaimResult.CLAIMED

    def test_fresh_claim_not_reclaimable(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.CLAIMED,
            idempotency_key="key-1",
            claimed_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
        result = claim_outbox(entry, stale_claim_seconds=300)
        assert result == OutboxClaimResult.ALREADY_CLAIMED


class TestMarkDeliveryOutcome:
    """Delivery outcome recording."""

    def test_mark_sent(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.CLAIMED,
            idempotency_key="key-1",
        )
        updated = mark_delivery_outcome(entry, DeliveryStatus.SENT, message_id="12345")
        assert updated.status == DeliveryStatus.SENT
        assert updated.message_id is None  # Not stored on OutboxEntry

    def test_mark_unknown(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.CLAIMED,
            idempotency_key="key-1",
        )
        updated = mark_delivery_outcome(entry, DeliveryStatus.UNKNOWN)
        assert updated.status == DeliveryStatus.UNKNOWN

    def test_mark_failed(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.CLAIMED,
            idempotency_key="key-1",
        )
        updated = mark_delivery_outcome(entry, DeliveryStatus.FAILED)
        assert updated.status == DeliveryStatus.FAILED

    def test_invalid_status_raises(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.PENDING,
            idempotency_key="key-1",
        )
        with pytest.raises(ValueError, match="terminal status"):
            mark_delivery_outcome(entry, DeliveryStatus.PENDING)

    def test_sent_sets_sent_at(self) -> None:
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.CLAIMED,
            idempotency_key="key-1",
        )
        now = datetime.now(timezone.utc)
        updated = mark_delivery_outcome(entry, DeliveryStatus.SENT, now=now)
        assert updated.sent_at == now


class TestDuplicateIdempotentOutbox:
    """Duplicate/idempotent outbox handling."""

    def test_same_idempotency_key_claimed_once(self) -> None:
        """Same idempotency key should not be processed twice."""
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.PENDING,
            idempotency_key="deliver:alert-1:v0",
        )
        # First claim
        result1 = claim_outbox(entry)
        assert result1 == OutboxClaimResult.CLAIMED

        # Simulate it was claimed (status changed)
        entry.status = DeliveryStatus.CLAIMED
        entry.claimed_at = datetime.now(timezone.utc)

        # Second claim — should be rejected
        result2 = claim_outbox(entry)
        assert result2 in (OutboxClaimResult.ALREADY_CLAIMED, OutboxClaimResult.ALREADY_SENT)


class TestUnknownDeliveryBehavior:
    """Ambiguous/unknown delivery handling."""

    def test_timeout_after_dispatch_marked_unknown(self) -> None:
        """After a timeout, delivery is UNKNOWN and not blindly resent."""
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.PENDING,
            idempotency_key="key-1",
        )
        # Simulate dispatch that timed out
        entry = mark_delivery_outcome(entry, DeliveryStatus.UNKNOWN)
        assert entry.status == DeliveryStatus.UNKNOWN

        # Second claim attempt — should be rejected (at-most-once)
        result = claim_outbox(entry)
        assert result == OutboxClaimResult.ALREADY_CLAIMED

    def test_unknown_entry_not_retried(self) -> None:
        """UNKNOWN entries are never blindly retried."""
        entry = OutboxEntry(
            id="1",
            alert_id="alert-1",
            status=DeliveryStatus.UNKNOWN,
            idempotency_key="key-1",
            sent_at=datetime.now(timezone.utc),
        )
        result = claim_outbox(entry)
        assert result == OutboxClaimResult.ALREADY_CLAIMED