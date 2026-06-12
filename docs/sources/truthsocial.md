# Truth Social Source Adapter

**Status**: MVP Implementation
**Source URL**: `https://truthsocial.com/@realDonaldTrump`
**Adapter class**: `TruthSocialAdapter`

## Overview

Acquires posts from @realDonaldTrump's Truth Social account using a
tiered acquisition strategy. Each successful fetch records the path
and latency for downstream transparency.

## Acquisition Order

1. **Direct API** (`direct_api`): Mastodon-compatible Truth Social status API
2. **Playwright** (`playwright`): Local persistent browser session fallback
3. **CNN Mirror** (`cnn_mirror`): `https://ix.cnn.io/data/truth-social/truth_archive.json`

## Fetch Paths

| Path | Description |
|------|-------------|
| `direct_api` | Direct Mastodon-compatible REST API |
| `playwright` | Local Playwright browser navigation |
| `cnn_mirror` | CNN archive at ~5-minute update latency |

## External ID Scheme

```
ts-<post_id>
```

Derived from the Truth Social numeric post ID, which is stable across
all fetch paths. The same post discovered via the API and the CNN mirror
will have the same external ID.

## Cross-Path Deduplication

Since external IDs are based on the Truth Social post ID, the same post
fetched via different paths produces the same external ID. Downstream
stages should prefer the earliest `detected_at` version.

## Versioning

Edited posts retain the same external ID but have different `text` and
`updated_at` values. Content hash changes signal a revised version.

## Latency and Degradation

- Direct API: < 1 second typical
- Playwright: ~3-5 seconds (browser launch/navigation)
- CNN Mirror: ~5 minutes behind live

When only the mirror path succeeds, the source should be marked
`DEGRADED` downstream.

## CAPTCHA and Proxies

No CAPTCHA solving or proxy bypass is implemented. If all paths fail,
the adapter raises an error and downstream should mark the source
degraded.

## Test Fixtures

- `tests/fixtures/sources/truthsocial_post.json` — 2 sample API posts
- `tests/fixtures/sources/truthsocial_cnn_mirror.json` — sample CNN mirror
  data with the same posts

## Contract Tests

- `tests/contract/sources/test_truthsocial.py`