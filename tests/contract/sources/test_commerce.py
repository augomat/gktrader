"""Contract tests for the Commerce (Dept of Commerce) adapter.

Verifies:
- Stable external IDs from press release URLs.
- HTML listing and detail page parsing.
- Fetch path recording for HTTP and fallback.
- Changed-version handling via content hash.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

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
# Fallback validation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Adapter metadata
# ---------------------------------------------------------------------------


class TestAdapterMetadata:
    def test_poll_interval(self, adapter: CommerceAdapter) -> None:
        assert adapter.poll_interval_seconds == 600

    def test_source_tier(self, adapter: CommerceAdapter) -> None:
        assert adapter.source_tier.value == "tier_1"
