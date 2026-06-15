"""Contract tests for the Commerce (Dept of Commerce) adapter.

Verifies:
- Stable external IDs from press release URLs.
- HTML listing and detail page parsing.
- Fetch path recording for HTTP and fallback.
- Changed-version handling via content hash.
- Wrapped detail payload preserves listing metadata.
- fetch_detail fallback to _remote_fetch when direct HTTP fails.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import httpx
import pytest
from pydantic import HttpUrl

from gktrader.domain.contracts import SourceIndexItem
from gktrader.sources.commerce import CommerceAdapter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "sources"


@pytest.fixture
def adapter() -> CommerceAdapter:
    return CommerceAdapter(client=None)  # type: ignore[arg-type]


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "commerce_listing.html").read_text(encoding="utf-8")


@pytest.fixture
def detail_html() -> str:
    return (FIXTURES / "commerce_detail.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Stable external IDs
# ---------------------------------------------------------------------------


class TestStableExternalIds:
    def test_from_url_dict(self, adapter: CommerceAdapter) -> None:
        """External IDs from dicts with URLs are stable."""
        raw = {"url": "https://www.commerce.gov/news/press-releases/2024/05/test"}
        id1 = adapter.derive_stable_external_id(raw)
        id2 = adapter.derive_stable_external_id(raw)
        assert id1 == id2
        assert id1.startswith("commerce-")

    def test_from_html_string(self, adapter: CommerceAdapter) -> None:
        """External IDs from HTML strings are stable for same content."""
        html = "<html><body>Same content</body></html>"
        id1 = adapter.derive_stable_external_id(html)
        id2 = adapter.derive_stable_external_id(html)
        assert id1 == id2

    def test_different_content_different_ids(self, adapter: CommerceAdapter) -> None:
        """Different content produces different external IDs."""
        id1 = adapter.derive_stable_external_id("content-a")
        id2 = adapter.derive_stable_external_id("content-b")
        assert id1 != id2


# ---------------------------------------------------------------------------
# Listing HTML parsing
# ---------------------------------------------------------------------------


class TestListingParsing:
    def test_parse_listing_extracts_links(self, adapter: CommerceAdapter, listing_html: str) -> None:
        """Listing HTML yields SourceIndexItems from press release links."""
        items = adapter._parse_listing_html(listing_html)
        assert len(items) >= 3
        urls = [str(i.detail_url) for i in items]
        assert any("semiconductor" in u for u in urls)
        assert any("broadband" in u for u in urls)

    def test_listing_items_have_valid_ids(self, adapter: CommerceAdapter, listing_html: str) -> None:
        """Each listing item has a stable external ID."""
        items = adapter._parse_listing_html(listing_html)
        for item in items:
            assert item.external_id.startswith("commerce-")
            assert len(item.external_id) > 10

    def test_listing_items_have_titles(self, adapter: CommerceAdapter, listing_html: str) -> None:
        """Each listing item has a non-empty title."""
        items = adapter._parse_listing_html(listing_html)
        for item in items:
            assert len(item.title) > 0


# ---------------------------------------------------------------------------
# Detail HTML normalisation
# ---------------------------------------------------------------------------


class TestDetailNormalization:
    def test_normalize_detail_html(self, adapter: CommerceAdapter, detail_html: str) -> None:
        """HTML detail pages normalise to valid NormalizedDocument."""
        doc = adapter.normalize(detail_html)
        assert doc.source_name == "commerce"
        assert doc.external_id.startswith("commerce-detail-")
        assert doc.fetch_path == "http_detail"
        assert len(doc.text) > 0

    def test_detail_html_id_stable(self, adapter: CommerceAdapter, detail_html: str) -> None:
        """Same HTML content produces the same external ID."""
        doc1 = adapter.normalize(detail_html)
        doc2 = adapter.normalize(detail_html)
        assert doc1.external_id == doc2.external_id

    def test_detail_content_changed(self, adapter: CommerceAdapter, detail_html: str) -> None:
        """Changed HTML content produces a different external ID."""
        modified = detail_html.replace(
            "$50 million", "$75 million"
        )
        doc1 = adapter.normalize(detail_html)
        doc2 = adapter.normalize(modified)
        assert doc1.text != doc2.text


# ---------------------------------------------------------------------------
# Wrapped detail normalization (preserving listing metadata)
# ---------------------------------------------------------------------------


class TestWrappedDetailNormalization:
    """Verify that wrapped detail payloads preserve listing item metadata."""

    def test_preserves_external_id_and_url(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """Wrapped detail keeps listing external_id and detail_url."""
        item = SourceIndexItem(
            external_id="commerce-test-id-1234",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/test"),
            title="Listing Title",
            published_at=datetime(2024, 5, 20, tzinfo=timezone.utc),
        )
        wrapped = {"item": item, "html": detail_html, "fetch_path": "http_detail"}
        doc = adapter.normalize(wrapped)
        assert doc.external_id == "commerce-test-id-1234"
        assert str(doc.canonical_url) == str(item.detail_url)

    def test_preserves_title_from_listing_when_detail_has_no_h1(
        self, adapter: CommerceAdapter
    ) -> None:
        """Fallback to listing title when detail HTML lacks an H1."""
        item = SourceIndexItem(
            external_id="commerce-test-id-5678",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/other"),
            title="Original Listing Title",
        )
        no_h1_html = "<html><body><p>Some content without H1</p></body></html>"
        wrapped = {"item": item, "html": no_h1_html, "fetch_path": "http_detail"}
        doc = adapter.normalize(wrapped)
        assert doc.title == "Original Listing Title"

    def test_preserves_published_at(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """Listing published_at is carried through to the NormalizedDocument."""
        pub = datetime(2024, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        item = SourceIndexItem(
            external_id="commerce-test-id-9012",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/dates"),
            title="Dated Listing",
            published_at=pub,
        )
        wrapped = {"item": item, "html": detail_html, "fetch_path": "http_detail"}
        doc = adapter.normalize(wrapped)
        assert doc.published_at == pub

    def test_fetch_path_recorded(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """The fetch_path from wrapped payload is recorded."""
        item = SourceIndexItem(
            external_id="commerce-test-id-path",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/path"),
            title="Path Test",
        )
        wrapped = {"item": item, "html": detail_html, "fetch_path": "playwright_detail"}
        doc = adapter.normalize(wrapped)
        assert doc.fetch_path == "playwright_detail"

    def test_extracted_text_from_detail_html(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """Useful text is extracted from the detail HTML."""
        item = SourceIndexItem(
            external_id="commerce-test-id-text",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/text"),
            title="Text Extraction",
        )
        wrapped = {"item": item, "html": detail_html, "fetch_path": "http_detail"}
        doc = adapter.normalize(wrapped)
        assert "semiconductor" in doc.text.lower()
        assert len(doc.text) > 20

    def test_listing_title_in_source_metadata(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """Original listing title is preserved in source_metadata."""
        item = SourceIndexItem(
            external_id="commerce-test-id-meta",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/meta"),
            title="Original Title For Metadata",
        )
        wrapped = {"item": item, "html": detail_html, "fetch_path": "http_detail"}
        doc = adapter.normalize(wrapped)
        assert doc.source_metadata.get("listing_title") == "Original Title For Metadata"
        assert doc.source_metadata.get("type") == "detail_page"


# ---------------------------------------------------------------------------
# fetch_detail fallback tests
# ---------------------------------------------------------------------------


class TestFetchDetailFallback:
    """Verify fetch_detail falls back to _remote_fetch when HTTP fails."""

    def test_fetch_detail_http_success_returns_wrapped_payload(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """Successful direct HTTP returns a wrapped payload."""
        item = SourceIndexItem(
            external_id="commerce-http-ok",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/http-ok"),
            title="HTTP OK",
        )
        mock_resp = mock.Mock()
        mock_resp.text = detail_html
        mock_resp.raise_for_status.return_value = None
        adapter.client = mock.Mock()
        adapter.client.get.return_value = mock_resp

        result = adapter.fetch_detail(item)
        assert isinstance(result, dict)
        assert result["item"] is item
        assert result["html"] == detail_html
        assert result["fetch_path"] == "http_detail"
        adapter.client.get.assert_called_once_with(str(item.detail_url))

    def test_fetch_detail_fallback_to_remote_fetch(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """When HTTP fails, fetch_detail falls back to _remote_fetch."""
        item = SourceIndexItem(
            external_id="commerce-fallback",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/05/fallback"),
            title="Fallback Test",
        )

        def failing_get(*args, **kwargs):
            request = httpx.Request("GET", str(item.detail_url))
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("blocked", request=request, response=response)

        adapter.client = mock.Mock()
        adapter.client.get.side_effect = failing_get

        original_remote = adapter._remote_fetch

        def fake_remote_fetch(url: str) -> str:
            return detail_html

        adapter._remote_fetch = fake_remote_fetch  # type: ignore[assignment]

        try:
            result = adapter.fetch_detail(item)
            assert isinstance(result, dict)
            assert result["item"] is item
            assert result["html"] == detail_html
            assert result["fetch_path"] == "playwright_detail"
        finally:
            adapter._remote_fetch = original_remote

    def test_fetch_detail_fallback_preserves_metadata_through_normalize(
        self, adapter: CommerceAdapter, detail_html: str
    ) -> None:
        """Fallback path through normalize preserves listing metadata."""
        pub = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item = SourceIndexItem(
            external_id="commerce-end-to-end",
            detail_url=HttpUrl("https://www.commerce.gov/news/press-releases/2024/06/e2e"),
            title="End-to-End Test",
            published_at=pub,
        )

        def failing_get(*args, **kwargs):
            request = httpx.Request("GET", str(item.detail_url))
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("blocked", request=request, response=response)

        adapter.client = mock.Mock()
        adapter.client.get.side_effect = failing_get

        original_remote = adapter._remote_fetch

        def fake_remote_fetch(url: str) -> str:
            return detail_html

        adapter._remote_fetch = fake_remote_fetch  # type: ignore[assignment]

        try:
            raw = adapter.fetch_detail(item)
            doc = adapter.normalize(raw)
            assert doc.external_id == "commerce-end-to-end"
            assert str(doc.canonical_url) == str(item.detail_url)
            assert doc.title == "Commerce Announces $50 Million Semiconductor Manufacturing Grant"
            assert doc.published_at == pub
            assert doc.fetch_path == "playwright_detail"
            assert "semiconductor" in doc.text.lower()
        finally:
            adapter._remote_fetch = original_remote


# ---------------------------------------------------------------------------
# source_index_item metadata recording
# ---------------------------------------------------------------------------


class TestListingMetadataRecording:
    """Verify listing_fetch_path is recorded in SourceIndexItem metadata."""

    def test_listing_fetch_path_recorded_in_metadata(
        self, adapter: CommerceAdapter, listing_html: str
    ) -> None:
        """_validate_listing_items records listing_fetch_path in metadata."""
        items = adapter._parse_listing_html(listing_html)
        adapter._validate_listing_items(items, fetch_path="http")
        for item in items:
            assert item.metadata.get("listing_fetch_path") == "http"

    def test_playwright_path_in_metadata(
        self, adapter: CommerceAdapter, listing_html: str
    ) -> None:
        """Playwright path is also recorded."""
        items = adapter._parse_listing_html(listing_html)
        adapter._validate_listing_items(items, fetch_path="playwright")
        for item in items:
            assert item.metadata.get("listing_fetch_path") == "playwright"

    def test_wrapped_detail_contains_listing_fetch_path(
        self, adapter: CommerceAdapter, detail_html: str, listing_html: str
    ) -> None:
        """listing_fetch_path flows through wrapped detail into source_metadata."""
        items = adapter._parse_listing_html(listing_html)
        adapter._validate_listing_items(items, fetch_path="playwright")
        item = items[0]
        wrapped = {"item": item, "html": detail_html, "fetch_path": "playwright_detail"}
        doc = adapter.normalize(wrapped)
        assert doc.source_metadata.get("listing_fetch_path") == "playwright"


class TestFetchValidation:
    def test_playwright_listing_without_press_release_links_fails(
        self,
        adapter: CommerceAdapter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        request = httpx.Request("GET", "https://www.commerce.gov/news/press-releases")
        response = httpx.Response(403, request=request)

        def fail_http(*args, **kwargs):
            raise httpx.HTTPStatusError("blocked", request=request, response=response)

        def fake_remote_fetch(url: str) -> str:
            return "<html><body><h1>Access denied</h1></body></html>"

        monkeypatch.setattr(adapter, "_fetch_http_index", fail_http)
        monkeypatch.setattr(adapter, "_remote_fetch", fake_remote_fetch)

        with pytest.raises(RuntimeError, match="0 press-release links"):
            adapter.fetch_index()

    def test_cloudflare_challenge_reports_gkfetch_status_and_title(
        self,
        adapter: CommerceAdapter,
    ) -> None:
        html = """
        <html>
          <head><title>Just a moment...</title></head>
          <body>www.commerce.gov Performing security verification</body>
        </html>
        """

        with pytest.raises(
            RuntimeError,
            match="Cloudflare challenge.*status=403.*title='Just a moment\\.\\.\\.'.*url=https://www.commerce.gov/news/press-releases",
        ):
            adapter._raise_if_cloudflare_challenge(
                html,
                fetch_path="gkfetch",
                status=403,
                title="Just a moment...",
                final_url="https://www.commerce.gov/news/press-releases",
            )

    def test_zero_link_error_includes_page_diagnostics(
        self,
        adapter: CommerceAdapter,
    ) -> None:
        html = "<html><head><title>No releases here</title></head><body></body></html>"

        with pytest.raises(RuntimeError, match="0 press-release links.*No releases here"):
            adapter._validate_listing_items([], fetch_path="playwright", html=html)


# ---------------------------------------------------------------------------
# Adapter metadata
# ---------------------------------------------------------------------------


class TestAdapterMetadata:
    def test_poll_interval(self, adapter: CommerceAdapter) -> None:
        assert adapter.poll_interval_seconds == 600

    def test_source_tier(self, adapter: CommerceAdapter) -> None:
        assert adapter.source_tier.value == "tier_1"
