"""Deterministic SEC-master/alias-based ticker resolution with conservative fuzzy matching."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz, process

from gktrader.domain.contracts import TickerCandidate

logger = logging.getLogger(__name__)

# Minimum score for fuzzy matching to produce a candidate
_FUZZY_MIN_SCORE = 70

# Mapping confidence threshold for TRADEABLE eligibility
TRADEABLE_CONFIDENCE_THRESHOLD = 0.90


@dataclass
class SecCompanyRecord:
    """A single entry from the SEC company tickers master."""

    ticker: str
    name: str
    cik: str
    exchange: str | None = None


@dataclass
class CompanyAlias:
    """A known alias for a company, with provenance tracking."""

    name: str
    normalized_name: str
    ticker: str
    cik: str
    provenance: str  # e.g. "sec_master", "curated", "former_name"
    confidence: float = 1.0


@dataclass
class TickerResolutionResult:
    """Result of a ticker resolution attempt for one company name."""

    company_name: str
    candidates: list[TickerCandidate] = field(default_factory=list)
    best_candidate: TickerCandidate | None = None
    ambiguous: bool = False
    error: str | None = None


class TickerResolver:
    """Deterministic company-to-ticker resolver.

    Resolution order:
    1. Exact normalized alias match.
    2. Exact SEC legal-name match.
    3. Validated curated alias match.
    4. Candidate generation through conservative fuzzy matching.
    5. Human review for unresolved or ambiguous candidates.

    The resolver never accepts LLM-provided tickers as validated mappings.
    """

    def __init__(self) -> None:
        self._sec_records: dict[str, SecCompanyRecord] = {}  # ticker -> record
        self._sec_by_name: dict[str, SecCompanyRecord] = {}  # normalized name -> record
        self._aliases: dict[str, CompanyAlias] = {}  # normalized alias name -> alias
        self._alias_by_ticker: dict[str, list[CompanyAlias]] = {}  # ticker -> aliases

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load_sec_master(self, records: list[SecCompanyRecord]) -> None:
        """Load SEC company ticker master data."""
        for rec in records:
            self._sec_records[rec.ticker.upper()] = rec
            norm = _normalize_name(rec.name)
            # Only set if not already present to keep first occurrence
            if norm not in self._sec_by_name:
                self._sec_by_name[norm] = rec

    def load_aliases(self, aliases: list[CompanyAlias]) -> None:
        """Load curated aliases."""
        for alias in aliases:
            key = alias.normalized_name
            self._aliases[key] = alias
            self._alias_by_ticker.setdefault(alias.ticker.upper(), []).append(alias)

    def get_sec_records(self) -> dict[str, SecCompanyRecord]:
        """Return the loaded SEC records keyed by ticker (uppercase)."""
        return dict(self._sec_records)

    def get_sec_by_name(self) -> dict[str, SecCompanyRecord]:
        """Return SEC records keyed by normalized company name."""
        return dict(self._sec_by_name)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, company_name: str) -> TickerResolutionResult:
        """Resolve a company name to ticker candidates.

        Args:
            company_name: Company name from the classifier output.

        Returns:
            TickerResolutionResult with candidates and resolution status.
        """
        result = TickerResolutionResult(company_name=company_name)
        normalized = _normalize_name(company_name)

        if not normalized:
            result.error = "Empty company name after normalization"
            return result

        # Step 1: Exact alias match
        alias = self._aliases.get(normalized)
        if alias is not None:
            candidate = TickerCandidate(
                company_name=company_name,
                normalized_name=normalized,
                ticker=alias.ticker,
                cik=alias.cik,
                is_active=True,
                is_public=True,
                confidence=alias.confidence,
                provenance=alias.provenance,
            )
            result.candidates.append(candidate)
            result.best_candidate = candidate
            return result

        # Step 2: Exact SEC legal-name match
        sec_rec = self._sec_by_name.get(normalized)
        if sec_rec is not None:
            candidate = TickerCandidate(
                company_name=company_name,
                normalized_name=normalized,
                ticker=sec_rec.ticker,
                cik=sec_rec.cik,
                exchange=sec_rec.exchange,
                is_active=True,
                is_public=True,
                confidence=1.0,
                provenance="sec_master",
            )
            result.candidates.append(candidate)
            result.best_candidate = candidate
            return result

        # Step 3: Fuzzy matching against all known names
        fuzzy_candidates = self._fuzzy_match(normalized)
        if fuzzy_candidates:
            result.candidates = fuzzy_candidates
            # Sort by confidence descending
            fuzzy_candidates.sort(key=lambda c: c.confidence, reverse=True)
            best = fuzzy_candidates[0]
            result.best_candidate = best

            # Mark ambiguous if the top two candidates are close
            if len(fuzzy_candidates) > 1:
                second = fuzzy_candidates[1]
                if best.confidence - second.confidence < 10:
                    result.ambiguous = True

        return result

    # ------------------------------------------------------------------
    # Fuzzy matching
    # ------------------------------------------------------------------

    def _fuzzy_match(self, normalized: str) -> list[TickerCandidate]:
        """Generate candidates through conservative fuzzy matching.

        Returns candidates sorted by confidence descending.
        """
        candidates: list[TickerCandidate] = []

        # Build search space from SEC names and aliases
        search_space: dict[str, tuple[str, str, str, str]] = {}
        for norm_name, rec in self._sec_by_name.items():
            search_space[norm_name] = (rec.ticker, rec.cik, "sec_master", rec.exchange or "")
        for norm_alias, alias in self._aliases.items():
            if norm_alias not in search_space:
                search_space[norm_alias] = (
                    alias.ticker,
                    alias.cik,
                    alias.provenance,
                    "",
                )

        if not search_space:
            return candidates

        scores: list[tuple[str, float]] = []
        for match_name in search_space:
            token_score = fuzz.token_sort_ratio(normalized, match_name)
            partial_score = fuzz.partial_ratio(normalized, match_name)
            # Partial/substring matches are useful candidates but should not
            # look fully validated.
            score = max(token_score, min(partial_score, 85))
            scores.append((match_name, score))

        scores.sort(key=lambda item: item[1], reverse=True)

        for match_name, score in scores[:5]:
            if score < _FUZZY_MIN_SCORE:
                continue
            ticker, cik, provenance, exchange = search_space[match_name]
            confidence = round(score / 100.0, 2)
            candidate = TickerCandidate(
                company_name=match_name,
                normalized_name=match_name,
                ticker=ticker,
                cik=cik,
                exchange=exchange or None,
                is_active=True,
                is_public=True,
                confidence=confidence,
                provenance=f"fuzzy_match:{provenance}",
            )
            candidates.append(candidate)

        return candidates


# ------------------------------------------------------------------
# Normalization helpers
# ------------------------------------------------------------------

_CORPORATE_SUFFIX_RE = re.compile(
    r"\b(?:incorporated|corporation|company|limited|international|holdings?|group)"
    r"|\b(?:inc|corp|co|ltd|llc|plc|lp|llp|intl)\.?\s*$",
    re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    """Normalize a company name for matching.

    - Lowercases
    - Strips leading/trailing whitespace
    - Collapses internal whitespace
    - Removes common corporate suffixes for comparison purposes
    """
    name = name.lower().strip()
    name = re.sub(r"\s+", " ", name)
    # Remove common legal suffixes for comparison (keeps original for display)
    name = _CORPORATE_SUFFIX_RE.sub("", name).strip()
    return name


def normalize_company_name(name: str) -> str:
    """Public normalization function for company names."""
    return name.lower().strip()
