"""Tests for canonical event fingerprinting."""

from __future__ import annotations

from datetime import date

from gktrader.intelligence.fingerprint import compute_event_fingerprint


class TestEventFingerprint:
    """Deterministic canonical event fingerprint tests."""

    def test_identical_inputs_produce_same_fingerprint(self) -> None:
        fp1 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        fp2 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        assert fp1 == fp2

    def test_different_inputs_produce_different_fingerprints(self) -> None:
        fp1 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        fp2 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bearish",
            action_status="announced",
        )
        assert fp1 != fp2

    def test_fingerprint_includes_ciks(self) -> None:
        fp1 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        fp2 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989", "0001652044"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        assert fp1 != fp2

    def test_sorted_ciks_are_deterministic(self) -> None:
        fp1 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001652044", "0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        fp2 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989", "0001652044"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        # The function sorts internally, so both orders produce same result
        assert fp1 == fp2

    def test_fingerprint_includes_award_ids(self) -> None:
        fp1 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
            award_or_contract_ids=["ABC-123"],
        )
        fp2 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
            award_or_contract_ids=["XYZ-789"],
        )
        assert fp1 != fp2

    def test_fingerprint_includes_monetary_amounts(self) -> None:
        fp1 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
            monetary_amounts=["$15M"],
        )
        fp2 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        assert fp1 != fp2

    def test_date_bucket_affects_fingerprint(self) -> None:
        fp1 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
            published_date=date(2025, 6, 1),
        )
        fp2 = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
            published_date=date(2025, 6, 2),
        )
        assert fp1 != fp2

    def test_output_format(self) -> None:
        fp = compute_event_fingerprint(
            sorted_ciks_or_tickers=["0001822989"],
            event_type="government_funding",
            direction="bullish",
            action_status="announced",
        )
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA-256 hex digest
        assert all(c in "0123456789abcdef" for c in fp)

    def test_empty_inputs_are_handled(self) -> None:
        fp = compute_event_fingerprint(
            sorted_ciks_or_tickers=[],
            event_type="irrelevant",
            direction="neutral",
            action_status="none",
        )
        assert isinstance(fp, str)
        assert len(fp) == 64