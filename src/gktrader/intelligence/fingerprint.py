"""Canonical event fingerprinting for deterministic deduplication."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Iterable


def compute_event_fingerprint(
    sorted_ciks_or_tickers: Iterable[str],
    event_type: str,
    direction: str,
    action_status: str,
    award_or_contract_ids: Iterable[str] | None = None,
    monetary_amounts: Iterable[str] | None = None,
    published_date: date | None = None,
) -> str:
    """Derive a deterministic SHA-256 fingerprint for a canonical event.

    The fingerprint is computed from:
    - Sorted validated CIKs or tickers.
    - Event type.
    - Direction.
    - Action status.
    - Normalized award, grant, or contract IDs (sorted).
    - Normalized material monetary amounts (sorted).
    - Published-date bucket (YYYY-MM-DD or empty string).

    All inputs are joined with a null-separator and hashed.

    Args:
        sorted_ciks_or_tickers: Sorted iterable of validated CIKs or tickers.
        event_type: The classifier event type string.
        direction: Bullish, bearish, neutral, or unclear.
        action_status: The action status string (e.g. "announced", "awarded").
        award_or_contract_ids: Optional list of award/contract IDs.
        monetary_amounts: Optional list of monetary amount strings.
        published_date: Optional published-date for date bucketing.

    Returns:
        A 64-character hex SHA-256 digest.
    """
    components: list[str] = []

    # Sorted identifiers
    ids = ",".join(sorted(sorted_ciks_or_tickers)) if sorted_ciks_or_tickers else ""
    components.append(ids)

    components.append(event_type.strip().lower())
    components.append(direction.strip().lower())
    components.append(action_status.strip().lower())

    # Award/contract IDs sorted
    if award_or_contract_ids:
        award_part = ",".join(sorted(award_or_contract_ids))
    else:
        award_part = ""
    components.append(award_part)

    # Monetary amounts sorted
    if monetary_amounts:
        amount_part = ",".join(sorted(monetary_amounts))
    else:
        amount_part = ""
    components.append(amount_part)

    # Date bucket
    if published_date is not None:
        if isinstance(published_date, datetime):
            published_date = published_date.date()
        date_part = published_date.isoformat()
    else:
        date_part = ""
    components.append(date_part)

    raw = "\0".join(components)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_str(s: str) -> str:
    """Normalize a string for fingerprinting (lowercase, strip)."""
    return s.strip().lower()