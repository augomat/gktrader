"""Contract tests for the NIST RSS adapter.

Verifies:
- Stable external IDs from RSS guid/URL.
- Timestamp parsing from RSS pubDate.
- Category metadata preservation (dc:subject, categories).
- Changed-version handling.
- HTML detail normalisation.
"""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

import feedparser
import pytest

from gktrader.sources.nist import NISTAdapter, _stable_id_from_url

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "sources"


@pytest.fixture
def adapter() -> NISTAdapter:
    return NISTAdapter(client=None)  # type: ignore[arg-type]


@pytest.fixture
def feed_xml() -> str:
    return (FIXTURES / "nist_feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "nist_article.html").read_text(encoding="utf-8")


@pytest.fixture
def parsed_entries(feed_xml: str) -> list:
    feed = feedparser.parse(feed_xml)
    return feed.entries


# ---------------------------------------------------------------------------
# Stable external IDs
# ---------------------------------------------------------------------------


class TestStableExternalIds:
    def test_rss_entry_id_stable(self, adapter: NISTAdapter, parsed_entries: list) -> None:
        """External IDs from NIST RSS are deterministic."""
        ids = [adapter.derive_stable_external_id(e) for e in parsed_entries]
        assert len(ids) == 3
        assert all(i.startswith("nist-") for i in ids)
        ids2 = [adapter.derive_stable_external_id(e) for e in parsed_entries]
        assert ids == ids2

    def test_external_id_from_url(self) -> None:
        """Same URL always produces the same external ID."""
        id1 = _stable_id_from_url("https://www.nist.gov/news/2024/03/quantum-computing-grant")
        id2 = _stable_id_from_url("https://www.nist.gov/news/2024/03/quantum-computing-grant")
        assert id1 == id2

    def test_different_urls_different_ids(self) -> None:
        id1 = _stable_id_from_url("https://www.nist.gov/a/")
        id2 = _stable_id_from_url("https://www.nist.gov/b/")
        assert id1 != id2


# ---------------------------------------------------------------------------
# Timestamps and metadata
# ---------------------------------------------------------------------------


class TestTimestampsAndMetadata:
    def test_rss_published_at(self, adapter: NISTAdapter, parsed_entries: list) -> None:
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.published_at is not None
        assert doc.published_at.tzinfo is not None
        assert doc.published_at.year == 2024

    def test_detected_at_set(self, adapter: NISTAdapter, parsed_entries: list) -> None:
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.detected_at.tzinfo is not None
        assert doc.detected_at.tzinfo == timezone.utc

    def test_categories_preserved(self, adapter: NISTAdapter, parsed_entries: list) -> None:
        """NIST dc:subject categories are preserved in source_metadata."""
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        cats = doc.source_metadata.get("categories", [])
        assert "Quantum" in cats
        assert "CHIPS" in cats

    def test_source_tier_and_name(self, adapter: NISTAdapter, parsed_entries: list) -> None:
        entry = parsed_entries[0]
        doc = adapter.normalize(entry)
        assert doc.source_name == "nist"
        assert doc.source_tier.value == "tier_1"


# ---------------------------------------------------------------------------
# Changed-version handling
# ---------------------------------------------------------------------------


class TestChangedVersionHandling:
    def test_content_change_detected(self, adapter: NISTAdapter) -> None:
        """Same external ID with different content = changed version."""
        entry1 = feedparser.parse(
            '<?xml version="1.0"?><rss version="2.0"><channel><item>'
            "<title>NIST Test</title><link>https://nist.gov/test</link>"
            "<description>Version 1</description></item></channel></rss>"
        ).entries[0]

        entry2 = feedparser.parse(
            '<?xml version="1.0"?><rss version="2.0"><channel><item>'
            "<title>NIST Test</title><link>https://nist.gov/test</link>"
            "<description>Version 2 (revised)</description></item></channel></rss>"
        ).entries[0]

        doc1 = adapter.normalize(entry1)
        doc2 = adapter.normalize(entry2)
        assert doc1.external_id == doc2.external_id
        assert doc1.text != doc2.text


# ---------------------------------------------------------------------------
# HTML detail normalisation
# ---------------------------------------------------------------------------


class TestHtmlDetail:
    def test_normalize_html_detail(self, adapter: NISTAdapter, article_html: str) -> None:
        doc = adapter.normalize(article_html)
        assert doc.source_name == "nist"
        assert doc.external_id.startswith("nist-detail-")
        assert doc.fetch_path == "rss_detail"
        assert len(doc.text) > 0

    def test_html_detail_id_stable(self, adapter: NISTAdapter, article_html: str) -> None:
        doc1 = adapter.normalize(article_html)
        doc2 = adapter.normalize(article_html)
        assert doc1.external_id == doc2.external_id

    def test_html_detail_contains_chips_context(self, adapter: NISTAdapter, article_html: str) -> None:
        doc = adapter.normalize(article_html)
        assert "CHIPS" in doc.text or "Quantum" in doc.text or "quantum" in doc.text


# ---------------------------------------------------------------------------
# Adapter metadata
# ---------------------------------------------------------------------------


class TestAdapterMetadata:
    def test_poll_interval(self, adapter: NISTAdapter) -> None:
        assert adapter.poll_interval_seconds == 60

    def test_source_tier(self, adapter: NISTAdapter) -> None:
        assert adapter.source_tier.value == "tier_1"