"""Contract tests for the SEC/8-K adapter.

Verifies:
- Stable external IDs from accession numbers and feed links.
- Timestamp parsing from Atom feed.
- Keyword prefilter matching against government-relevant terms.
- Company ticker master parsing via parse_ticker_master().
- SEC User-Agent behaviour.
- Filing detail HTML normalisation.
- Changed-version handling.
- Rate-limit awareness demonstrated.
"""

from __future__ import annotations

import json
from pathlib import Path

import feedparser
import pytest

from gktrader.sources.sec import SECAdapter, _extract_accession, pad_cik

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "sources"


@pytest.fixture
def adapter() -> SECAdapter:
    return SECAdapter(client=None)  # type: ignore[arg-type]


@pytest.fixture
def feed_xml() -> str:
    return (FIXTURES / "sec_8k_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def ticker_master_json() -> str:
    return (FIXTURES / "sec_company_tickers.json").read_text(encoding="utf-8")


@pytest.fixture
def filing_html() -> str:
    return (FIXTURES / "sec_filing_detail.html").read_text(encoding="utf-8")


@pytest.fixture
def parsed_entries(feed_xml: str) -> list:
    feed = feedparser.parse(feed_xml)
    return feed.entries


# ---------------------------------------------------------------------------
# Stable external IDs
# ---------------------------------------------------------------------------


class TestStableExternalIds:
    def test_from_accession_number(self, adapter: SECAdapter, parsed_entries: list) -> None:
        """External IDs are derived from accession numbers when available."""
        ids = [adapter.derive_stable_external_id(e) for e in parsed_entries]
        assert len(ids) == 3
        assert all(i.startswith("sec-8k-") or i.startswith("sec-") for i in ids)
        # Re-derivation is stable
        ids2 = [adapter.derive_stable_external_id(e) for e in parsed_entries]
        assert ids == ids2

    def test_accession_extracted_from_url(self) -> None:
        """Accession numbers are extracted from SEC EDGAR URLs."""
        url = "https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123-index.html"
        acc = _extract_accession(url)
        assert acc == "0000320193-24-000123"

    def test_accession_none_for_non_sec_url(self) -> None:
        assert _extract_accession("https://example.com") is None

    def test_same_filing_same_id(self, adapter: SECAdapter) -> None:
        """Same filing link always produces the same external ID."""
        entries = feedparser.parse(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            "<title>8-K: Test</title>"
            '<link href="https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123-index.html"/>'
            "</entry></feed>"
        ).entries
        id1 = adapter.derive_stable_external_id(entries[0])
        id2 = adapter.derive_stable_external_id(entries[0])
        assert id1 == id2


# ---------------------------------------------------------------------------
# Timestamps and metadata
# ---------------------------------------------------------------------------


class TestTimestampsAndMetadata:
    def test_published_at(self, adapter: SECAdapter, parsed_entries: list) -> None:
        """Published timestamp is parsed from Atom feed."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.published_at is not None
        assert doc.published_at.year == 2024

    def test_updated_at_present(self, adapter: SECAdapter, parsed_entries: list) -> None:
        """Updated timestamps are captured when available."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.updated_at is not None
        assert doc.updated_at > doc.published_at  # type: ignore[operator]

    def test_source_metadata_accession(self, adapter: SECAdapter, parsed_entries: list) -> None:
        """Accession number is preserved in source_metadata."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert "accession_number" in doc.source_metadata
        assert len(doc.source_metadata["accession_number"]) > 0


# ---------------------------------------------------------------------------
# Keyword prefilter
# ---------------------------------------------------------------------------


class TestKeywordPrefilter:
    def test_prefilter_matches_contract(self) -> None:
        """Terms like 'contract' trigger the prefilter."""
        assert SECAdapter.matches_prefilter("Government contract awarded")
        assert SECAdapter.matches_prefilter("Material definitive agreement")

    def test_prefilter_matches_government(self) -> None:
        assert SECAdapter.matches_prefilter("Federal agency funding")
        assert SECAdapter.matches_prefilter("Department of Defense award")

    def test_prefilter_does_not_match_irrelevant(self) -> None:
        assert not SECAdapter.matches_prefilter("Change in board composition")
        assert not SECAdapter.matches_prefilter("Routine compensation update")
        assert not SECAdapter.matches_prefilter("")

    def test_prefilter_matches_filing_items(self) -> None:
        """SEC filing item numbers trigger the prefilter."""
        assert SECAdapter.matches_prefilter("Item 1.01 Entry into Agreement")
        assert SECAdapter.matches_prefilter("Item 2.05 Costs Associated")

    def test_prefilter_matches_summary(self) -> None:
        """Prefilter searches both title and summary."""
        assert SECAdapter.matches_prefilter("Unrelated title", "Government contract mentioned")


# ---------------------------------------------------------------------------
# Company ticker master
# ---------------------------------------------------------------------------


class TestTickerMaster:
    def test_parse_ticker_master(self, adapter: SECAdapter, ticker_master_json: str) -> None:
        """parse_ticker_master returns sorted company records."""
        companies = SECAdapter.parse_ticker_master(ticker_master_json)
        assert len(companies) == 9
        assert companies[0]["ticker"] == "AAPL"
        assert companies[0]["cik"] == "320193"
        assert companies[0]["cik_padded"] == "0000320193"

    def test_ticker_master_structure(self, ticker_master_json: str) -> None:
        """Every entry has cik, ticker, name, cik_padded."""
        companies = SECAdapter.parse_ticker_master(ticker_master_json)
        for c in companies:
            assert "cik" in c
            assert "cik_padded" in c
            assert "ticker" in c
            assert "name" in c
            assert len(c["cik_padded"]) == 10

    def test_ticker_master_dict_input(self, ticker_master_json: str) -> None:
        """Works with both string and dict input."""
        data = json.loads(ticker_master_json)
        companies = SECAdapter.parse_ticker_master(data)
        assert len(companies) == 9


# ---------------------------------------------------------------------------
# CIK padding
# ---------------------------------------------------------------------------


class TestCikPadding:
    def test_pad_cik(self) -> None:
        assert pad_cik(320193) == "0000320193"
        assert pad_cik("320193") == "0000320193"
        assert pad_cik("789019") == "0000789019"

    def test_pad_cik_short(self) -> None:
        assert pad_cik(2488) == "0000002488"
        assert pad_cik("50863") == "0000050863"


# ---------------------------------------------------------------------------
# Filing detail normalisation
# ---------------------------------------------------------------------------


class TestFilingDetail:
    def test_normalize_filing_html(self, adapter: SECAdapter, filing_html: str) -> None:
        """SEC filing HTML normalises to a valid NormalizedDocument."""
        doc = adapter.normalize(filing_html)
        assert doc.source_name == "sec_8k"
        assert doc.external_id.startswith("sec-detail-")
        assert doc.fetch_path == "filing_detail"
        assert len(doc.text) > 0
        # The filing mentions a government contract
        assert "Defense" in doc.text or "contract" in doc.text

    def test_filing_detail_id_stable(self, adapter: SECAdapter, filing_html: str) -> None:
        """Same filing HTML produces the same external ID."""
        doc1 = adapter.normalize(filing_html)
        doc2 = adapter.normalize(filing_html)
        assert doc1.external_id == doc2.external_id


# ---------------------------------------------------------------------------
# Changed-version handling
# ---------------------------------------------------------------------------


class TestChangedVersionHandling:
    def test_amended_filing_different_content(self, adapter: SECAdapter) -> None:
        """An amended filing has the same link but different content."""
        entry1 = feedparser.parse(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            "<title>8-K: Test Corp</title>"
            '<link href="https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123-index.html"/>'
            "<summary>Initial filing: contract award $500M</summary>"
            "</entry></feed>"
        ).entries[0]

        entry2 = feedparser.parse(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            "<title>8-K: Test Corp</title>"
            '<link href="https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123-index.html"/>'
            "<summary>Amendment: contract value increased to $750M</summary>"
            "</entry></feed>"
        ).entries[0]

        doc1 = adapter.normalize(entry1)
        doc2 = adapter.normalize(entry2)
        assert doc1.external_id == doc2.external_id
        assert doc1.text != doc2.text


# ---------------------------------------------------------------------------
# Adapter metadata
# ---------------------------------------------------------------------------


class TestAdapterMetadata:
    def test_poll_interval(self, adapter: SECAdapter) -> None:
        assert adapter.poll_interval_seconds == 60

    def test_source_tier(self, adapter: SECAdapter) -> None:
        assert adapter.source_tier.value == "tier_1"

    def test_user_agent_default(self) -> None:
        """Default User-Agent is set."""
        adapter = SECAdapter(client=None)  # type: ignore[arg-type]
        assert "GKTrader" in adapter._user_agent


# ---------------------------------------------------------------------------
# Rate-limit awareness
# ---------------------------------------------------------------------------


class TestRateLimitAwareness:
    def test_min_interval_constant(self) -> None:
        """SEC_MIN_INTERVAL is set to stay under 10 req/s."""
        from gktrader.sources.sec import SEC_MIN_INTERVAL

        # 10 req/s = 0.1s between requests; we use 0.15 as safety margin
        assert SEC_MIN_INTERVAL >= 0.1
        assert SEC_MIN_INTERVAL < 1.0