"""Contract tests for the Truth Social adapter.

Verifies:
- Stable external IDs from Truth Social post IDs.
- Cross-path deduplication (same post from API and CNN mirror).
- Fetch path recording.
- Timestamp and metadata extraction.
- CNN mirror fallback normalisation.
- Changed-version handling.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gktrader.sources.truthsocial import TruthSocialAdapter

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "sources"


@pytest.fixture
def adapter() -> TruthSocialAdapter:
    return TruthSocialAdapter(client=None)  # type: ignore[arg-type]


@pytest.fixture
def api_posts() -> list[dict]:
    data = json.loads((FIXTURES / "truthsocial_post.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture
def cnn_mirror_data() -> dict:
    return json.loads(
        (FIXTURES / "truthsocial_cnn_mirror.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Stable external IDs
# ---------------------------------------------------------------------------


class TestStableExternalIds:
    def test_api_post_id(self, adapter: TruthSocialAdapter, api_posts: list[dict]) -> None:
        """External IDs from API posts are stable and deterministic."""
        ids = [adapter.derive_stable_external_id(p) for p in api_posts]
        assert len(ids) == 2
        assert ids[0] == "ts-113456789012345678"
        assert ids[1] == "ts-113456789012345679"
        # Re-derivation is stable
        ids2 = [adapter.derive_stable_external_id(p) for p in api_posts]
        assert ids == ids2

    def test_cnn_mirror_post_id(self, adapter: TruthSocialAdapter, cnn_mirror_data: dict) -> None:
        """External IDs from CNN mirror are stable."""
        posts = cnn_mirror_data["data"]
        ids = [adapter.derive_stable_external_id(p) for p in posts]
        assert len(ids) == 2
        assert ids[0] == "ts-113456789012345678"  # Same post ID as API
        assert ids[1] == "ts-113456789012345679"


# ---------------------------------------------------------------------------
# Normalisation — API path
# ---------------------------------------------------------------------------


class TestApiNormalization:
    def test_api_normalize_sets_fetch_path(self, adapter: TruthSocialAdapter, api_posts: list[dict]) -> None:
        """Documents normalised from API have fetch_path='direct_api'."""
        doc = adapter.normalize(api_posts[0])
        assert doc.fetch_path == "direct_api"

    def test_api_normalize_metadata(self, adapter: TruthSocialAdapter, api_posts: list[dict]) -> None:
        """API metadata fields are preserved."""
        doc = adapter.normalize(api_posts[0])
        assert doc.source_name == "truthsocial"
        assert doc.source_tier.value == "tier_1"
        assert doc.source_metadata["post_id"] == "113456789012345678"
        assert doc.source_metadata["visibility"] == "public"

    def test_api_normalize_timestamps(self, adapter: TruthSocialAdapter, api_posts: list[dict]) -> None:
        """Published and updated timestamps are extracted."""
        doc = adapter.normalize(api_posts[0])
        assert doc.published_at is not None
        assert doc.published_at.year == 2024

        # Second post was edited
        doc2 = adapter.normalize(api_posts[1])
        assert doc2.updated_at is not None
        assert doc2.updated_at > doc2.published_at  # type: ignore[operator]

    def test_api_normalize_text_content(self, adapter: TruthSocialAdapter, api_posts: list[dict]) -> None:
        """Text content is extracted from the API response."""
        doc = adapter.normalize(api_posts[0])
        assert "American manufacturing" in doc.text
        assert doc.title.endswith("…")  # truncated


# ---------------------------------------------------------------------------
# Normalisation — CNN mirror fallback
# ---------------------------------------------------------------------------


class TestCnnMirrorNormalization:
    def test_cnn_mirror_normalize(self, adapter: TruthSocialAdapter, cnn_mirror_data: dict) -> None:
        """CNN mirror posts normalise correctly with fallback fetch path."""
        post = cnn_mirror_data["data"][0]
        doc = adapter.normalize_cnn_mirror_post(post)
        assert doc.fetch_path == "cnn_mirror"
        assert doc.source_name == "truthsocial"
        assert "American manufacturing" in doc.text

    def test_cnn_mirror_metadata(self, adapter: TruthSocialAdapter, cnn_mirror_data: dict) -> None:
        """Mirror-specific metadata is preserved."""
        post = cnn_mirror_data["data"][0]
        doc = adapter.normalize_cnn_mirror_post(post)
        assert doc.source_metadata["source"] == "cnn_mirror"
        assert doc.source_metadata["original_id"] == "113456789012345678"
        assert "archived_at" in doc.source_metadata["mirror_timestamp"]

    def test_cnn_mirror_external_id_stable(self, adapter: TruthSocialAdapter, cnn_mirror_data: dict) -> None:
        """Same mirror post content produces same external ID."""
        post = cnn_mirror_data["data"][0]
        doc1 = adapter.normalize_cnn_mirror_post(post)
        doc2 = adapter.normalize_cnn_mirror_post(post)
        assert doc1.external_id == doc2.external_id


# ---------------------------------------------------------------------------
# Cross-path deduplication
# ---------------------------------------------------------------------------


class TestCrossPathDeduplication:
    def test_same_post_different_paths_same_id(
        self, adapter: TruthSocialAdapter, api_posts: list[dict], cnn_mirror_data: dict
    ) -> None:
        """The same Truth Social post has the same external ID
        regardless of which fetch path discovered it."""
        api_doc = adapter.normalize(api_posts[0])
        mirror_doc = adapter.normalize_cnn_mirror_post(cnn_mirror_data["data"][0])
        assert api_doc.external_id == mirror_doc.external_id


class TestPlaywrightParsing:
    def test_playwright_normalizes_volatile_pinned_prefix(self, adapter: TruthSocialAdapter) -> None:
        first = (
            "Pinned Truth Donald J. Trump @realDonaldTrump · 16h "
            "Barack Hussein Obama’s Deal with Iran, the JCPOA, was an easy, terrible disaster"
        )
        second = (
            "Pinned Truth Donald J. Trump @realDonaldTrump · 15h "
            "Barack Hussein Obama’s Deal with Iran, the JCPOA, was an easy, terrible disaster"
        )

        items_first = adapter._parse_text_listing(first)
        items_second = adapter._parse_text_listing(second)

        assert len(items_first) == 1
        assert len(items_second) == 1
        assert items_first[0].external_id == items_second[0].external_id
        assert items_first[0].title.startswith("Barack Hussein Obama")
        assert str(items_first[0].detail_url) == "https://truthsocial.com/@realDonaldTrump"
        assert "Pinned Truth" not in items_first[0].title
        assert "16h" not in items_first[0].title

    def test_index_fallback_normalize_keeps_full_playwright_text(self, adapter: TruthSocialAdapter) -> None:
        raw = (
            "Donald J. Trump @realDonaldTrump · 7h Congratulations to Jim Dolan and the New York "
            "Knicks!!! What a year it has been and there is much more to come in the future."
        )

        item = adapter._parse_text_listing(raw)[0]
        doc = adapter.normalize(item)

        assert doc.fetch_path == "index_fallback"
        assert "What a year it has been" in doc.text
        assert len(doc.text) > len(doc.title)
        assert doc.source_metadata["normalized_line"] == doc.text

    # ------------------------------------------------------------------
    # Engagement-counter rejection, stripping, and stable identity
    # ------------------------------------------------------------------

    def test_rejects_counter_only_line(self, adapter: TruthSocialAdapter) -> None:
        """A line that is purely an engagement counter (e.g. '45.2K') is rejected."""
        items = adapter._parse_text_listing("45.2K")
        assert len(items) == 0

    def test_rejects_multiple_counters_line(self, adapter: TruthSocialAdapter) -> None:
        """A line containing only counter values is rejected entirely."""
        items = adapter._parse_text_listing("45.2K  12.3K  1,234")
        assert len(items) == 0

    def test_rejects_unsuffixed_counter_fragment_line(self, adapter: TruthSocialAdapter) -> None:
        """Observed fragments like '842 767 2.86k' are rejected entirely."""
        items = adapter._parse_text_listing("842 767 2.86k")
        assert len(items) == 0

    def test_rejects_single_counter_lowercase(self, adapter: TruthSocialAdapter) -> None:
        """A line that is purely a lowercase-suffix counter is rejected."""
        items = adapter._parse_text_listing("99.9k")
        assert len(items) == 0

    def test_rejects_short_numeric_line(self, adapter: TruthSocialAdapter) -> None:
        """A very short line that is purely numeric is rejected."""
        items = adapter._parse_text_listing("123")
        assert len(items) == 0

    def test_strips_trailing_counters_from_valid_post(self, adapter: TruthSocialAdapter) -> None:
        """Trailing engagement counters are stripped from otherwise valid post text."""
        raw = (
            "Donald J. Trump @realDonaldTrump · 2h "
            "Big news coming for American manufacturing! "
            "45.2K  12.3K  1,234"
        )
        items = adapter._parse_text_listing(raw)
        assert len(items) == 1, "expected exactly one item"
        title = items[0].title
        assert "Big news coming for American manufacturing" in title
        assert "45.2K" not in title
        assert "12.3K" not in title
        assert "1,234" not in title

    def test_strips_observed_trailing_counter_shape(self, adapter: TruthSocialAdapter) -> None:
        """Trailing counters are stripped when only the final token has a suffix."""
        raw = (
            "Donald J. Trump @realDonaldTrump · 2h "
            "Big news coming for American manufacturing! "
            "842 767 2.86k"
        )
        items = adapter._parse_text_listing(raw)
        assert len(items) == 1
        assert items[0].title == "Big news coming for American manufacturing!"

    def test_trailing_counters_do_not_change_stable_id(self, adapter: TruthSocialAdapter) -> None:
        """The same post text with different trailing counters gets the same external_id."""
        raw_a = (
            "Donald J. Trump @realDonaldTrump · 2h "
            "We are winning like never before! "
            "85.2K  15.3K"
        )
        raw_b = (
            "Donald J. Trump @realDonaldTrump · 3h "
            "We are winning like never before! "
            "92.1K  18.7K  2,345"
        )
        items_a = adapter._parse_text_listing(raw_a)
        items_b = adapter._parse_text_listing(raw_b)
        assert len(items_a) == 1
        assert len(items_b) == 1
        assert items_a[0].external_id == items_b[0].external_id
        # Both should retain the same core text after stripping counters
        assert items_a[0].title == items_b[0].title
        assert "We are winning like never before!" in items_a[0].title

    def test_counter_lines_among_valid_posts(self, adapter: TruthSocialAdapter) -> None:
        """Counter-only lines interspersed with real posts do not become items."""
        raw = (
            "Donald J. Trump @realDonaldTrump · 1h America is back!\n"
            "45.2K\n"
            "12.3K\n"
            "Donald J. Trump @realDonaldTrump · 2h We will not back down ever!\n"
            "99.1K\n"
            "1,234"
        )
        items = adapter._parse_text_listing(raw)
        assert len(items) == 2
        assert "America is back!" in items[0].title
        assert "We will not back down ever!" in items[1].title


# ---------------------------------------------------------------------------
# Changed-version handling
# ---------------------------------------------------------------------------


class TestChangedVersionHandling:
    def test_edited_post_different_content(self, adapter: TruthSocialAdapter, api_posts: list[dict]) -> None:
        """An edited version of the same post has different text
        but the same external ID."""
        original = api_posts[0]
        edited = dict(original)
        edited["content"] = "UPDATED: " + original["content"]
        edited["edited_at"] = "2024-06-10T16:00:00.000Z"

        doc_orig = adapter.normalize(original)
        doc_edited = adapter.normalize(edited)

        assert doc_orig.external_id == doc_edited.external_id
        assert doc_orig.text != doc_edited.text


# ---------------------------------------------------------------------------
# Adapter metadata
# ---------------------------------------------------------------------------


class TestAdapterMetadata:
    def test_poll_interval(self, adapter: TruthSocialAdapter) -> None:
        assert adapter.poll_interval_seconds == 600

    def test_source_tier(self, adapter: TruthSocialAdapter) -> None:
        assert adapter.source_tier.value == "tier_1"


# ---------------------------------------------------------------------------
# Production ingest path: fetch_index -> fetch_detail -> normalize
# ---------------------------------------------------------------------------
#
# Regression for the round-2 bug where the generic pipeline handed an
# index-shaped dict from fetch_detail to _normalize_api_post, which raised
# HttpUrl("") on every item (so Truth Social ingested nothing in production).


class TestProductionIngestPath:
    def test_cnn_mirror_fetch_detail_normalize_roundtrip(
        self, adapter: TruthSocialAdapter, cnn_mirror_data: dict
    ) -> None:
        """The exact production loop (fetch_detail -> normalize) must succeed
        for every cnn-mirror item, preserve full text, and record the
        cnn_mirror fetch path."""
        items = adapter._parse_cnn_mirror(cnn_mirror_data)
        assert items, "expected parsed mirror items"

        docs = [adapter.normalize(adapter.fetch_detail(item)) for item in items]
        assert all(d.fetch_path == "cnn_mirror" for d in docs)
        assert all(str(d.canonical_url) for d in docs)
        # Full post text is preserved (not just the 120-char title).
        assert any("American manufacturing" in d.text for d in docs)

    def test_parse_cnn_mirror_preserves_raw_and_bounds(
        self, adapter: TruthSocialAdapter
    ) -> None:
        """The mirror archive is bounded per poll and each item keeps its raw
        post so normalize can recover the full content."""
        from gktrader.sources.truthsocial import MAX_MIRROR_ITEMS

        big = {
            "data": [
                {
                    "id": str(i),
                    "text": f"post number {i}",
                    "url": "https://truthsocial.com/@realDonaldTrump/" + str(i),
                    "created_at": f"2024-06-{(i % 28) + 1:02d}T10:00:00.000Z",
                }
                for i in range(MAX_MIRROR_ITEMS + 25)
            ]
        }
        items = adapter._parse_cnn_mirror(big)
        assert len(items) == MAX_MIRROR_ITEMS
        assert all(it.metadata.get("raw") is not None for it in items)
