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
        assert adapter.poll_interval_seconds == 60

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