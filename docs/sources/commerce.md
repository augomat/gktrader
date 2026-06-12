# Commerce (Department of Commerce) Source Adapter

**Status**: MVP Implementation
**Source URL**: `https://www.commerce.gov/news/press-releases`
**Adapter class**: `CommerceAdapter`

## Overview

Fetches Department of Commerce press releases. Uses direct HTTP first,
with a Playwright browser fallback when Cloudflare blocks direct requests.

## Acquisition Order

1. **HTTP** (`http`): Direct HTTP request to the press releases listing page
2. **Playwright** (`playwright`): Browser-based fallback when HTTP fails

## Fetch Paths

| Path | Description |
|------|-------------|
| `http` | Direct HTTP listing page fetch |
| `http_detail` | HTTP fetch of a press release detail page |
| `playwright` | Playwright browser rendering fallback |
| `fallback` | Dict-based fallback normalisation |

## External ID Scheme

```
commerce-<sha256(url)[:16]>
```

Derived from the press release URL or path, which is stable across polls.

## Versioning

Detail page content changes are detected via content hash. External IDs
remain tied to the URL.

## Constraints

- No CAPTCHA solving or proxy bypass is implemented.
- When Cloudflare blocks all paths, the adapter raises `RuntimeError`.
- Downstream should mark the source degraded after repeated failures.

## Test Fixtures

- `tests/fixtures/sources/commerce_listing.html` — sample listing page
  with 3 press release links
- `tests/fixtures/sources/commerce_detail.html` — sample press release
  detail page

## Contract Tests

- `tests/contract/sources/test_commerce.py`