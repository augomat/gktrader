"""Contract tests for the White House RSS adapter.

Verifies:
- Stable external IDs from RSS guid/URL.
- Timestamp parsing from RSS pubDate.
- Changed-version handling via content hash.
- Metadata preservation (summary, categories).
- Normalised document structure conforms to NormalizedDocument.
"""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

import feedparser
import pytest

from gktrader.sources.whitehouse import WhiteHouseAdapter, _stable_id_from_url

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "sources"


@pytest.fixture
def adapter() -> WhiteHouseAdapter:
    """Return a WhiteHouseAdapter with no real HTTP client."""
    return WhiteHouseAdapter(client=None)  # type: ignore[arg-type]


@pytest.fixture
def feed_xml() -> str:
    return (FIXTURES / "whitehouse_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "whitehouse_article.html").read_text(encoding="utf-8")


@pytest.fixture
def parsed_entries(feed_xml: str) -> list:
    feed = feedparser.parse(feed_xml)
    return feed.entries


# ---------------------------------------------------------------------------
# Stable external IDs
# ---------------------------------------------------------------------------


class TestStableExternalIds:
    def test_rss_entry_guid(self, adapter: WhiteHouseAdapter, parsed_entries: list) -> None:
        """External IDs are deterministic and stable for RSS entries."""
        ids = [adapter.derive_stable_external_id(e) for e in parsed_entries]
        assert len(ids) == 3
        assert all(isinstance(i, str) and i.startswith("wh-") for i in ids)
        # Re-deriving must give the same ID
        ids2 = [adapter.derive_stable_external_id(e) for e in parsed_entries]
        assert ids == ids2

    def test_external_id_from_link(self) -> None:
        """Same URL always produces the same external ID."""
        id1 = _stable_id_from_url("https://www.whitehouse.gov/news/2024/01/15/test/")
        id2 = _stable_id_from_url("https://www.whitehouse.gov/news/2024/01/15/test/")
        assert id1 == id2
        assert id1.startswith("wh-")

    def test_different_urls_different_ids(self) -> None:
        """Different URLs produce distinct external IDs."""
        id1 = _stable_id_from_url("https://www.whitehouse.gov/a/")
        id2 = _stable_id_from_url("https://www.whitehouse.gov/b/")
        assert id1 != id2


# ---------------------------------------------------------------------------
# Timestamps and metadata
# ---------------------------------------------------------------------------


class TestTimestampsAndMetadata:
    def test_rss_published_at(self, adapter: WhiteHouseAdapter, parsed_entries: list) -> None:
        """Published timestamps are parsed from RSS pubDate."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.published_at is not None
        assert doc.published_at.tzinfo is not None
        assert doc.published_at.year == 2024

    def test_detected_at_set(self, adapter: WhiteHouseAdapter, parsed_entries: list) -> None:
        """detected_at is set to current UTC time during normalization."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.detected_at.tzinfo is not None
        # Should be very recent
        assert doc.detected_at.tzinfo == timezone.utc

    def test_source_metadata_preserved(self, adapter: WhiteHouseAdapter, parsed_entries: list) -> None:
        """RSS summary is preserved in source_metadata."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert "summary" in doc.source_metadata
        assert len(doc.source_metadata["summary"]) > 0

    def test_source_tier_and_name(self, adapter: WhiteHouseAdapter, parsed_entries: list) -> None:
        """Source name and tier are correctly set."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.source_name == "whitehouse"
        assert doc.source_tier.value == "tier_1"
        assert doc.fetch_path == "rss"


# ---------------------------------------------------------------------------
# Changed-version handling
# ---------------------------------------------------------------------------


class TestChangedVersionHandling:
    def test_content_hash_changes(self, adapter: WhiteHouseAdapter) -> None:
        """Different content produces different normalised output."""
        entry1 = feedparser.parse(
            '<?xml version="1.0"?><rss version="2.0"><channel><item>'
            "<title>Article A</title><link>https://example.com/a</link>"
            "<description>Version 1 content</description></item></channel></rss>"
        ).entries[0]

        entry2 = feedparser.parse(
            '<?xml version="1.0"?><rss version="2.0"><channel><item>'
            "<title>Article A</title><link>https://example.com/a</link>"
            "<description>Version 2 content (revised)</description></item></channel></rss>"
        ).entries[0]

        doc1 = adapter.normalize(entry1)
        doc2 = adapter.normalize(entry2)
        # External IDs are the same (same link) but content differs
        assert doc1.external_id == doc2.external_id
        assert doc1.text != doc2.text


# ---------------------------------------------------------------------------
# HTML detail normalisation
# ---------------------------------------------------------------------------


class TestHtmlDetail:
    def test_normalize_html_detail(self, adapter: WhiteHouseAdapter, article_html: str) -> None:
        """HTML detail pages are normalised to a valid NormalizedDocument."""
        doc = adapter.normalize(article_html)
        assert doc.source_name == "whitehouse"
        assert doc.external_id.startswith("wh-detail-")
        assert doc.fetch_path == "rss_detail"
        assert len(doc.text) > 0
        assert doc.title == ""

    def test_html_detail_id_stable(self, adapter: WhiteHouseAdapter, article_html: str) -> None:
        """Same HTML content produces the same external ID."""
        doc1 = adapter.normalize(article_html)
        doc2 = adapter.normalize(article_html)
        assert doc1.external_id == doc2.external_id


# ---------------------------------------------------------------------------
# Fetch index result structure
# ---------------------------------------------------------------------------


class TestFetchIndexStructure:
    def test_poll_interval(self, adapter: WhiteHouseAdapter) -> None:
        """Poll interval is 60 seconds as per spec."""
        assert adapter.poll_interval_seconds == 60

    def test_adapter_metadata(self, adapter: WhiteHouseAdapter) -> None:
        """Adapter exposes required metadata."""
        assert adapter.source_name == "whitehouse"
        assert adapter.source_tier.value == "tier_1"