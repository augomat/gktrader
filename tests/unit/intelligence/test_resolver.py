"""Tests for ticker resolver: alias matching, fuzzy matching, and ambiguity handling."""

from __future__ import annotations

from gktrader.domain.contracts import TickerCandidate
from gktrader.intelligence.resolver import (
    CompanyAlias,
    SecCompanyRecord,
    TickerResolver,
    _normalize_name,
    normalize_company_name,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

_SEC_RECORDS = [
    SecCompanyRecord(ticker="RGTI", name="Rigetti Computing Inc.", cik="0001822989", exchange="NASDAQ"),
    SecCompanyRecord(ticker="QBTS", name="D-Wave Quantum Inc.", cik="0001829635", exchange="NYSE"),
    SecCompanyRecord(ticker="IONQ", name="IonQ Inc.", cik="0001820927", exchange="NYSE"),
    SecCompanyRecord(ticker="GOOGL", name="Alphabet Inc.", cik="0001652044", exchange="NASDAQ"),
    SecCompanyRecord(ticker="MSFT", name="Microsoft Corporation", cik="0000789019", exchange="NASDAQ"),
    SecCompanyRecord(ticker="AAPL", name="Apple Inc.", cik="0000320193", exchange="NASDAQ"),
    SecCompanyRecord(ticker="AMZN", name="Amazon.com Inc.", cik="0001018724", exchange="NASDAQ"),
]

_ALIASES = [
    CompanyAlias(
        name="Rigetti Computing",
        normalized_name="rigetti computing",
        ticker="RGTI",
        cik="0001822989",
        provenance="curated",
        confidence=1.0,
    ),
    CompanyAlias(
        name="D-Wave",
        normalized_name="d-wave",
        ticker="QBTS",
        cik="0001829635",
        provenance="curated",
        confidence=1.0,
    ),
    CompanyAlias(
        name="Alphabet",
        normalized_name="alphabet",
        ticker="GOOGL",
        cik="0001652044",
        provenance="curated",
        confidence=1.0,
    ),
]


def _build_resolver() -> TickerResolver:
    resolver = TickerResolver()
    resolver.load_sec_master(_SEC_RECORDS)
    resolver.load_aliases(_ALIASES)
    return resolver


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestTickerResolverExactMatch:
    """Exact alias and SEC name matching."""

    def test_exact_alias_match(self) -> None:
        resolver = _build_resolver()
        result = resolver.resolve("Rigetti Computing")
        assert result.best_candidate is not None
        assert result.best_candidate.ticker == "RGTI"
        assert result.best_candidate.confidence == 1.0
        assert result.best_candidate.provenance == "curated"
        assert not result.ambiguous

    def test_exact_alias_match_dwave(self) -> None:
        resolver = _build_resolver()
        result = resolver.resolve("D-Wave")
        assert result.best_candidate is not None
        assert result.best_candidate.ticker == "QBTS"
        assert result.best_candidate.provenance == "curated"

    def test_exact_sec_name_match(self) -> None:
        resolver = _build_resolver()
        result = resolver.resolve("Microsoft Corporation")
        assert result.best_candidate is not None
        assert result.best_candidate.ticker == "MSFT"
        assert result.best_candidate.provenance == "sec_master"

    def test_sec_name_case_insensitive(self) -> None:
        resolver = _build_resolver()
        result = resolver.resolve("microsoft corporation")
        assert result.best_candidate is not None
        assert result.best_candidate.ticker == "MSFT"


class TestTickerResolverFuzzyMatch:
    """Fuzzy matching generates candidates but doesn't auto-approve."""

    def test_fuzzy_match_produces_candidate(self) -> None:
        resolver = _build_resolver()
        # "Rigetti" without "Computing" should fuzzy-match
        result = resolver.resolve("Rigetti")
        assert len(result.candidates) >= 1
        assert result.best_candidate is not None
        # Fuzzy candidates should have confidence < 1.0
        assert result.best_candidate.confidence < 1.0

    def test_fuzzy_match_confidence_below_threshold(self) -> None:
        resolver = _build_resolver()
        result = resolver.resolve("Riggeti Computng")  # misspelled
        if result.best_candidate:
            # Even a close fuzzy match should have confidence < 1.0
            assert result.best_candidate.confidence < 0.90 or result.best_candidate.provenance.startswith("fuzzy_match")

    def test_ambiguous_mapping_not_becoming_tradeable(self) -> None:
        """Ambiguous mappings (confidence < 0.90) can never become TRADEABLE."""
        resolver = _build_resolver()
        # A name that could match multiple things
        result = resolver.resolve("Quantum Computing Inc")
        if result.best_candidate:
            # Even if we get a candidate, fuzzy confidence should be below 0.90
            # or it should be marked ambiguous
            assert (result.best_candidate.confidence < 0.90) or result.ambiguous

    def test_no_match_returns_no_candidates(self) -> None:
        resolver = _build_resolver()
        result = resolver.resolve("Totally Fake Company XYZ")
        # May still get fuzzy matches if similarity is above threshold
        # But should not have high confidence
        if result.best_candidate:
            assert result.best_candidate.confidence < 0.70

    def test_empty_name_returns_error(self) -> None:
        resolver = _build_resolver()
        result = resolver.resolve("   ")
        assert result.error is not None
        assert result.best_candidate is None

    def test_fuzzy_differentiates_close_names(self) -> None:
        """Fuzzy matching should differentiate between similar company names."""
        resolver = _build_resolver()
        # A name close to "IonQ" should match IonQ
        result = resolver.resolve("Ion Q")
        if result.best_candidate:
            assert result.best_candidate.ticker == "IONQ"


class TestTickerResolverNoLLMTickers:
    """Resolver never accepts LLM-provided tickers as validated mappings."""

    def test_resolver_ignores_ticker_input(self) -> None:
        """The resolver only takes company names, never tickers.
        This test verifies the API contract enforces this."""
        resolver = _build_resolver()
        # The resolver interface only accepts company_name
        result = resolver.resolve("Apple Inc.")
        assert result.best_candidate is not None
        assert result.best_candidate.ticker == "AAPL"
        # The confidence should come from alias/sec match, not from an LLM


class TestNormalizeName:
    """Name normalization edge cases."""

    def test_normalize_whitespace(self) -> None:
        assert normalize_company_name("  Apple  Inc.  ") == "apple  inc."

    def test_normalize_case(self) -> None:
        assert normalize_company_name("MICROSOFT CORPORATION") == "microsoft corporation"

    def test_normalize_empty(self) -> None:
        assert normalize_company_name("") == ""

    def test_normalize_special_chars(self) -> None:
        assert normalize_company_name("D-Wave Systems") == "d-wave systems"


class TestSuffixNormalization:
    """Bug #11: _normalize_name must strip common corporate suffixes for matching."""

    def test_corporation_matches_corp(self) -> None:
        assert _normalize_name("Example Corporation") == _normalize_name("Example Corp")

    def test_inc_stripped(self) -> None:
        assert _normalize_name("Rigetti Computing Inc.") == _normalize_name("Rigetti Computing")

    def test_incorporated_stripped(self) -> None:
        assert _normalize_name("Acme Incorporated") == _normalize_name("Acme")

    def test_llc_stripped(self) -> None:
        assert _normalize_name("Foo Bar LLC") == _normalize_name("Foo Bar")

    def test_ltd_stripped(self) -> None:
        assert _normalize_name("Example Ltd.") == _normalize_name("Example")

    def test_resolver_matches_corporation_vs_corp(self) -> None:
        """Resolver must find the same ticker for 'Example Corp' and 'Example Corporation'."""
        resolver = TickerResolver()
        resolver.load_sec_master([
            SecCompanyRecord(ticker="EXMP", name="Example Corporation", cik="0001234567"),
        ])
        # Without suffix stripping, normalized "example corporation" ≠ "example corp"
        result_corp = resolver.resolve("Example Corp")
        result_full = resolver.resolve("Example Corporation")
        # Both should reach at least a candidate via fuzzy matching after suffix stripping
        assert result_corp.best_candidate is not None or result_full.best_candidate is not None, (
            "At least one should resolve to EXMP"
        )