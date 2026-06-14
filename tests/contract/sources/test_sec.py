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
- Filing index page parsing for primary 8-K document extraction.
- Two-hop fetch_detail returning wrapped payload with index and document.
- Wrapped detail normalization preserving feed metadata, URLs, and filing text.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import feedparser
import pytest

from gktrader.domain.contracts import HttpUrl, SourceIndexItem
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
def filing_index_html() -> str:
    """HTML of the EDGAR filing index page with document table."""
    return (FIXTURES / "sec_filing_index.html").read_text(encoding="utf-8")


@pytest.fixture
def parsed_entries(feed_xml: str) -> list:
    feed = feedparser.parse(feed_xml)
    return feed.entries


@pytest.fixture
def wrapped_detail(filing_html: str) -> dict:
    """Simulated wrapped detail payload as returned by fetch_detail()."""
    item = SourceIndexItem(
        external_id="sec-8k-0000320193-24-000123",
        detail_url=HttpUrl(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "0000320193-24-000123-index.html"
        ),
        title="8-K: Apple Inc. files current report regarding government contract award",
        published_at=datetime(2024, 6, 10, 16, 30, 0, tzinfo=timezone.utc),
        updated_at=datetime(2024, 6, 10, 16, 35, 0, tzinfo=timezone.utc),
        metadata={
            "summary": (
                "Apple Inc. filed a Form 8-K reporting a material definitive "
                "agreement with a federal agency."
            ),
            "accession_number": "0000320193-24-000123",
            "prefilter_match": True,
        },
    )
    return {
        "item": item,
        "index_url": str(item.detail_url),
        "filing_url": (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "0000320193-24-000123/filing-8k.htm"
        ),
        "html": filing_html,
    }


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
# Filing index page parsing
# ---------------------------------------------------------------------------


class TestFilingIndexParsing:
    def test_parse_filing_index_finds_8k(self, adapter: SECAdapter, filing_index_html: str) -> None:
        """_parse_filing_index extracts the primary 8-K document link from the document table."""
        base_url = (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "0000320193-24-000123-index.html"
        )
        url = adapter._parse_filing_index(filing_index_html, base_url)
        assert url is not None
        assert url.endswith("filing-8k.htm")

    def test_parse_filing_index_returns_none_when_no_table(self, adapter: SECAdapter) -> None:
        """_parse_filing_index returns None when there is no document table."""
        assert adapter._parse_filing_index("<html></html>", "http://example.com") is None

    def test_parse_filing_index_prefers_8k_over_exhibits(self, adapter: SECAdapter, filing_index_html: str) -> None:
        """The first 8-K row is selected, not an exhibit row."""
        base_url = "https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123-index.html"
        url = adapter._parse_filing_index(filing_index_html, base_url)
        assert url is not None
        assert "filing-8k" in url
        assert "exhibit" not in url

    def test_parse_filing_index_absolute_url_generation(self, adapter: SECAdapter, filing_index_html: str) -> None:
        """The returned URL is absolute (https://www.sec.gov/...)."""
        base_url = (
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "0000320193-24-000123-index.html"
        )
        url = adapter._parse_filing_index(filing_index_html, base_url)
        assert url is not None
        assert url.startswith("https://www.sec.gov/")

    def test_parse_filing_index_unwraps_inline_xbrl_viewer(self, adapter: SECAdapter) -> None:
        """Viewer URLs are unwrapped to the raw filing document."""
        index_html = """
        <html><body>
        <table class="tableFile" summary="Document Format Files">
          <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
          <tr>
            <td>1</td>
            <td>8-K</td>
            <td><a href="/ix?doc=/Archives/edgar/data/1018724/000110465926073562/tm2613616d5_8k.htm">tm2613616d5_8k.htm</a></td>
            <td>8-K</td>
          </tr>
        </table>
        </body></html>
        """
        base_url = (
            "https://www.sec.gov/Archives/edgar/data/1018724/"
            "000110465926073562/0001104659-26-073562-index.htm"
        )

        url = adapter._parse_filing_index(index_html, base_url)

        assert url == (
            "https://www.sec.gov/Archives/edgar/data/1018724/"
            "000110465926073562/tm2613616d5_8k.htm"
        )


# ---------------------------------------------------------------------------
# Wrapped detail normalisation
# ---------------------------------------------------------------------------


class TestWrappedDetailNormalization:
    def test_preserves_external_id(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """Wrapped detail normalization preserves the feed-derived external_id."""
        doc = adapter.normalize(wrapped_detail)
        assert doc.external_id == "sec-8k-0000320193-24-000123"

    def test_preserves_title(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """Wrapped detail normalization preserves the feed title."""
        doc = adapter.normalize(wrapped_detail)
        assert "Apple Inc." in doc.title
        assert "government contract award" in doc.title

    def test_preserves_published_at(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """Wrapped detail normalization preserves published_at from feed."""
        doc = adapter.normalize(wrapped_detail)
        assert doc.published_at is not None
        assert doc.published_at.year == 2024

    def test_preserves_updated_at(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """Wrapped detail normalization preserves updated_at from feed."""
        doc = adapter.normalize(wrapped_detail)
        assert doc.updated_at is not None
        assert doc.updated_at > doc.published_at  # type: ignore[operator]

    def test_canonical_url_is_filing_url(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """canonical_url points to the actual filing document."""
        doc = adapter.normalize(wrapped_detail)
        assert "filing-8k.htm" in str(doc.canonical_url)

    def test_contains_actual_filing_text(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """The normalized text is the filing document text, not index-page content."""
        doc = adapter.normalize(wrapped_detail)
        assert len(doc.text) > 0
        # The filing HTML fixture mentions Defense / contract
        assert "Defense" in doc.text or "contract" in doc.text

    def test_preserves_accession_number(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """Accession number is preserved in source_metadata."""
        doc = adapter.normalize(wrapped_detail)
        assert doc.source_metadata.get("accession_number") == "0000320193-24-000123"

    def test_preserves_prefilter_match(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """prefilter_match is preserved in source_metadata."""
        doc = adapter.normalize(wrapped_detail)
        assert doc.source_metadata.get("prefilter_match") is True

    def test_stores_index_and_filing_urls(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """index_url and filing_url are stored in source_metadata."""
        doc = adapter.normalize(wrapped_detail)
        assert "index_url" in doc.source_metadata
        assert "filing_url" in doc.source_metadata
        assert doc.source_metadata["filing_url"].endswith("filing-8k.htm")
        assert doc.source_metadata["index_url"].endswith("-index.html")

    def test_fetch_path_is_filing_detail(self, adapter: SECAdapter, wrapped_detail: dict) -> None:
        """fetch_path is 'filing_detail' for wrapped details."""
        doc = adapter.normalize(wrapped_detail)
        assert doc.fetch_path == "filing_detail"


# ---------------------------------------------------------------------------
# fetch_detail two-hop
# ---------------------------------------------------------------------------


class TestFetchDetail:
    def test_returns_wrapped_dict(self, adapter: SECAdapter, filing_index_html: str, filing_html: str) -> None:
        """fetch_detail returns a dict with item, index_url, filing_url, and html."""
        item = SourceIndexItem(
            external_id="sec-8k-0000320193-24-000123",
            detail_url=HttpUrl(
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "0000320193-24-000123-index.html"
            ),
            title="8-K: Apple Inc.",
            published_at=datetime(2024, 6, 10, 16, 30, 0, tzinfo=timezone.utc),
            updated_at=datetime(2024, 6, 10, 16, 35, 0, tzinfo=timezone.utc),
            metadata={"accession_number": "0000320193-24-000123", "prefilter_match": True},
        )

        idx_resp = MagicMock()
        idx_resp.text = filing_index_html
        idx_resp.raise_for_status = MagicMock()

        filing_resp = MagicMock()
        filing_resp.text = filing_html
        filing_resp.raise_for_status = MagicMock()

        with patch.object(adapter, "_request") as mock_request:
            mock_request.side_effect = [idx_resp, filing_resp]
            result = adapter.fetch_detail(item)

        assert isinstance(result, dict)
        assert result["item"] is item
        assert result["index_url"] == str(item.detail_url)
        assert "filing-8k.htm" in result["filing_url"]
        assert result["html"] == filing_html

    def test_two_hops_made(self, adapter: SECAdapter, filing_index_html: str, filing_html: str) -> None:
        """fetch_detail makes exactly two HTTP requests: index page and filing document."""
        item = SourceIndexItem(
            external_id="sec-8k-0000000000-24-000000",
            detail_url=HttpUrl(
                "https://www.sec.gov/Archives/edgar/data/0/"
                "0000000000-24-000000-index.html"
            ),
            title="8-K: Test",
            metadata={"accession_number": "0000000000-24-000000", "prefilter_match": True},
        )

        idx_resp = MagicMock()
        idx_resp.text = filing_index_html
        idx_resp.raise_for_status = MagicMock()

        filing_resp = MagicMock()
        filing_resp.text = filing_html
        filing_resp.raise_for_status = MagicMock()

        with patch.object(adapter, "_request") as mock_request:
            mock_request.side_effect = [idx_resp, filing_resp]
            adapter.fetch_detail(item)

        # Two requests made: index + filing document
        assert mock_request.call_count == 2
        # First request is the index URL
        assert "-index.html" in mock_request.call_args_list[0][0][0]
        # Second request is the filing document
        assert "filing-8k" in mock_request.call_args_list[1][0][0]

    def test_fetch_detail_requests_raw_filing_not_inline_xbrl_viewer(
        self, adapter: SECAdapter, filing_html: str
    ) -> None:
        """fetch_detail should request the raw filing body when the index uses ix?doc."""
        item = SourceIndexItem(
            external_id="sec-8k-0001104659-26-073562",
            detail_url=HttpUrl(
                "https://www.sec.gov/Archives/edgar/data/1018724/"
                "000110465926073562/0001104659-26-073562-index.htm"
            ),
            title="8-K: Test",
            metadata={"accession_number": "0001104659-26-073562", "prefilter_match": True},
        )

        index_resp = MagicMock()
        index_resp.text = """
        <html><body>
        <table class="tableFile" summary="Document Format Files">
          <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
          <tr>
            <td>1</td>
            <td>8-K</td>
            <td><a href="/ix?doc=/Archives/edgar/data/1018724/000110465926073562/tm2613616d5_8k.htm">tm2613616d5_8k.htm</a></td>
            <td>8-K</td>
          </tr>
        </table>
        </body></html>
        """
        index_resp.raise_for_status = MagicMock()

        filing_resp = MagicMock()
        filing_resp.text = filing_html
        filing_resp.raise_for_status = MagicMock()

        with patch.object(adapter, "_request") as mock_request:
            mock_request.side_effect = [index_resp, filing_resp]
            result = adapter.fetch_detail(item)

        assert mock_request.call_args_list[1][0][0] == (
            "https://www.sec.gov/Archives/edgar/data/1018724/"
            "000110465926073562/tm2613616d5_8k.htm"
        )
        assert result["filing_url"] == (
            "https://www.sec.gov/Archives/edgar/data/1018724/"
            "000110465926073562/tm2613616d5_8k.htm"
        )


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
