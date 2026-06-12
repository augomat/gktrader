# GKTrader — Round 2 Bugs (Production-Path & Live-Credential Validation)

Second validation pass of `src/gktrader` against `IMPLEMENTATION_PLAN.md`. The
**12 bugs in `docs/BUGS_FOUND.md` are all fixed** and the 448-test suite passes.

This round is different: a real `.env` with **working live credentials** is now
present, so each finding below was **reproduced against the real services**
(OpenRouter, Alpaca, SEC, White House, NIST, Truth Social/CNN mirror, Telegram).

The headline: **the unit tests pass, but the wired-up production pipeline
(`tasks/jobs.py` → `tasks/pipeline.py` → real adapters) is broken in two
load-bearing places.** The tests pass because they build the pipeline with
hand-fed, pre-loaded objects; the production builder (`_build_pipeline`) does not.

Fix in the order listed (top = most damaging). When you fix one, add a test that
exercises the **production wiring**, not just the isolated helper.

What was confirmed *working* live (do not "fix" these):
- OpenRouter classifier with the configured `google/gemini-3.1-flash-lite` and the
  strict `json_schema` request — returns a valid `ClassifierResult`.
- Alpaca IEX snapshot parsing — keys (`latestTrade`/`prevDailyBar`/`dailyBar`) match,
  price/prev-close/move/volume all parse.
- White House, NIST, SEC 8-K RSS — fetch + `fetch_detail` + `normalize` end-to-end.
- Telegram bot token (`getMe` → bot `richy_trading_bot`), owner ID present.
- SEC ticker master fetch — 10,416 rows, and "Intel Corporation" resolves to **INTC**
  at confidence 1.0 *when the master is loaded*.

---

> **Status update (2026-06-12):** Items **#1, #2, and #3 are now FIXED** and
> verified live + covered by new regression tests (suite 448 → 452). Items #4–#7
> remain open. Details inline below.

## 1. ✅ FIXED — CRITICAL — The ticker resolver is empty in production, so nothing can ever become TRADEABLE and market data is requested with the wrong symbol

**Where:** `src/gktrader/tasks/jobs.py:41-61` (`_build_pipeline`), line 54:

```python
resolver = TickerResolver()          # <-- created empty, never loaded
```

`grep` confirms `load_sec_master(...)` and `load_aliases(...)` are **only ever
called in tests**. The production resolver has an empty SEC master and no aliases.

**Reproduced live:**

```python
from gktrader.intelligence.resolver import TickerResolver
TickerResolver().resolve("Intel Corporation").best_candidate   # -> None
```

vs. with the master loaded (also live-verified, 10,416 rows from
`SECAdapter.fetch_ticker_master`):

```python
# Intel Corporation -> ticker INTC, confidence 1.0
```

**What this breaks downstream (all in `tasks/pipeline.py`):**
- `process_documents` → every `EventCompany.mapping_confidence = 0.0`,
  `mapping_status = "ambiguous"`.
- `create_signals` → `ticker` falls back to the raw company name (e.g.
  `"Intel Corporation"`), `best_mapping = 0.0`.
- Scoring: `mapping_confidence 0.0 < 0.90` ⇒ the §11 TRADEABLE gate can **never**
  pass. **No alert can ever be TRADEABLE.** Everything tops out at REVIEW.
- `create_alerts` calls `self._snapshot(ticker)` with `ticker="Intel Corporation"`
  → Alpaca 404/empty for that "symbol" → snapshot fails → forced REVIEW + a
  useless paper trade keyed on a company name instead of a ticker.

**Spec impact:** §10 "Deterministic Company and Ticker Resolution", the §11
TRADEABLE gate ("Validated active public ticker", "Mapping confidence at least
0.90"), DoD "Tickers are deterministically validated", golden scenarios #1 and #4.
The core deterministic-validation feature is dead.

**Fix:**
1. In `_build_pipeline`, after constructing the resolver, load the SEC master:
   ```python
   import httpx
   from gktrader.intelligence.resolver import TickerResolver, SecCompanyRecord
   client = httpx.Client(timeout=30)
   recs = SECAdapter.fetch_ticker_master(client, settings.sec_user_agent)
   client.close()
   resolver = TickerResolver()
   resolver.load_sec_master([
       SecCompanyRecord(ticker=r["ticker"], name=r["name"], cik=str(r["cik"]))
       for r in recs
   ])
   ```
   Cache it (e.g. module-level, or a DB-backed `companies` table) so you do **not**
   re-download on every 60-second poll — that would also blow SEC rate limits.
2. **Also missing:** there is **no scheduled task** that refreshes the SEC
   ticker/CIK master daily (§9 "Refresh SEC ticker/CIK data daily."). Add a Celery
   Beat entry (e.g. once per day) that reloads the master into the cache/`companies`
   table, and have `_build_pipeline` read from that cache.

Add a test that runs `_build_pipeline()` (or its resolver) and asserts
`resolve("Intel Corporation").best_candidate.ticker == "INTC"`.

**Fix applied:** `tasks/jobs.py` now has `_load_resolver` / `_get_resolver` —
a per-process cache (24h TTL, thread-locked) that fetches the SEC master via
`SECAdapter.fetch_ticker_master` and loads it into the resolver; `_build_pipeline`
uses it. On fetch failure it serves the previous resolver, or an empty one
without caching so the next poll retries. A daily Celery Beat task
`refresh_ticker_master` (03:00) forces a reload (§9). Verified live: 10,416 rows,
Intel→INTC/NVIDIA→NVDA/Apple→AAPL @1.0, Rigetti→RGTI @0.97, cache reused on 2nd
call. Tests: `TestResolverWiring` in `tests/unit/test_tasks.py`.

---

## 2. ✅ FIXED — CRITICAL — Truth Social ingestion fails for every item (and the source it relies on returns the entire archive)

**Where:** `src/gktrader/sources/truthsocial.py` — `fetch_detail` (line 160-163)
vs. `normalize` dispatch (line 169-176), as consumed by
`pipeline.ingest_sources` (`tasks/pipeline.py:322-345`).

**What happens (reproduced live):**
The production ingest loop does `raw = adapter.fetch_detail(item)` then
`adapter.normalize(raw)`. For Truth Social:
- `fetch_detail(item)` returns `item.model_dump()`, i.e. a dict with keys
  `external_id, detail_url, title, published_at, updated_at, metadata`.
- `normalize(dict)` sees a `dict` and routes to `_normalize_api_post`, which expects
  **Mastodon API keys** (`content`, `url`/`uri`, `id`, `created_at`). None exist in
  the index-item dict, so `url = post.get("url") or post.get("uri","")` is `""` and
  `HttpUrl("")` raises `ValidationError`.

Result, verified end-to-end against the live CNN mirror:

```
fetch_path: cnn_mirror  items: 33905
normalize ERROR: ValidationError ... Input should be a valid URL, input is empty
```

So **every** Truth Social item throws in `ingest_sources` (caught per-item and
appended to `result.errors`). Net effect: **zero Truth Social documents ingested,
~34k errors recorded per poll.** Truth Social — the spec's primary, fastest signal
source (`@realDonaldTrump`) — is completely non-functional.

The root cause is a contract mismatch: the CNN-mirror/index path produces
`SourceIndexItem`s, but `_normalize_api_post` only understands raw API posts, and
`_parse_cnn_mirror` (line 279) does **not** stash the original post text in
`metadata` (it keeps only `post_id`), so the content is already lost by the time
`normalize` runs. There is a `normalize_cnn_mirror_post` method that does the right
thing, but the pipeline never calls it.

**Spec impact:** §9 Truth Social adapter, golden scenario #7 ("Truth Social direct
failure falls back to CNN mirror and displays fetch path and latency"), DoD "five
MVP source families run".

**Fix applied:** `_parse_cnn_mirror`/`_parse_api_statuses` keep the full original
post under `metadata["raw"]`; `fetch_detail` returns `{"_path": <path>, "post": <raw>}`;
`normalize` routes `SourceIndexItem` → `_normalize_from_index`, wrapped cnn-mirror
dicts → `normalize_cnn_mirror_post`, api/bare dicts → `_normalize_api_post`.
Verified live: 50/50 normalize, `fetch_path="cnn_mirror"`, full text + URLs preserved.
Tests: `TestProductionIngestPath` in `tests/contract/sources/test_truthsocial.py`.

**Original fix guidance (do both):**
- Make `fetch_detail` return something `normalize` can actually use. Simplest:
  return the `SourceIndexItem` itself (not `.model_dump()`) so `normalize` routes to
  `_normalize_from_index` (which uses `item.title`/`item.detail_url`). Better:
  have `_parse_cnn_mirror` keep the full post under `metadata["raw"]`, and route
  cnn-mirror dicts to `normalize_cnn_mirror_post` so the full post text and the
  correct `fetch_path="cnn_mirror"` are preserved (the latter matters for the §11
  −2 fetch-path-delay penalty and golden #7).
- Add a contract test that drives `fetch_index → fetch_detail → normalize` for the
  CNN-mirror path (the existing tests call `normalize_cnn_mirror_post` directly and
  therefore miss this).

---

## 3. ✅ FIXED — HIGH — CNN mirror returns the full 33,905-item archive on every poll, unbounded

**Where:** `src/gktrader/sources/truthsocial.py:135-154` (`_fetch_cnn_mirror`) and
`_parse_cnn_mirror` (line 279).

`_fetch_cnn_mirror` accepts a `cursor` argument but **ignores it**, and
`_parse_cnn_mirror` returns *every* post in the archive. Live, that is **33,905
items**. `ingest_sources` then, per item, calls `fetch_detail` + `normalize` +
`_hash_text` + a `RawDocument` existence query — i.e. ~34k DB round-trips **every
60 seconds**, and on the very first poll it would attempt to insert ~34k
`raw_documents` rows. (Right now bug #2 makes them all fail first, but once #2 is
fixed this becomes the dominant problem.)

**Spec impact:** §9 "Keep separate cursors per acquisition path"; the 60-second
poll budget; first-start baseline sanity.

**Fix applied:** `_parse_cnn_mirror` now sorts posts by `created_at` (tz-safe) and
keeps only the most recent `MAX_MIRROR_ITEMS = 50`, so a poll is bounded to 50
fetch/normalize/DB ops; dedup on `(source_name, external_id, content_hash)` stays
the idempotency guard. Verified live: 33,905 → 50 items. **Remaining nice-to-have:**
a persisted per-path cursor so steady-state polls fetch only genuinely-new posts
(currently it re-fetches the newest 50 each poll and relies on dedup to skip them).

---

## 4. MEDIUM — Playwright fallback paths are never wired, so Commerce (and Truth Social tier-2) have no working fallback

**Where:** `src/gktrader/tasks/jobs.py:46-52`. Adapters are built with no browser
context:

```python
"commerce": CommerceAdapter(),
"truthsocial": TruthSocialAdapter(),   # browser_context defaults to None
```

`TruthSocialAdapter._fetch_playwright` raises immediately when
`self._browser_context` is falsy, and Commerce behaves the same way. Live, Commerce
fails with `All Commerce acquisition paths failed` (its HTTP path hits the
spec-documented Cloudflare 403, and the Playwright path can't run). `playwright`
*is* installed in the venv — it's just never instantiated/passed in.

**Spec impact:** §9 Commerce + Truth Social "Local Playwright persistent browser
session" tier; §16 health "Mark a source degraded ... one degradation
notification and one recovery notification". Currently Commerce will just emit a
hard failure each poll with no working middle tier.

**Fix:** Construct a persistent Playwright browser context once (per worker) and
inject it into the adapters that support it. If Playwright is intentionally
out-of-scope for first deploy, then make the degradation path explicit: Commerce
should mark itself degraded and emit exactly one degrade/recover notification
(per §16) rather than throwing every cycle.

---

## 5. MEDIUM — `active_public_ticker` is satisfied by an unvalidated raw company name (fail-open risk)

**Where:** `src/gktrader/tasks/pipeline.py:624`.

```python
active_public_ticker=bool(ticker),   # ticker may be the raw LLM company name
```

`ticker` here can be the unvalidated company name (see bug #1). So the §11
TRADEABLE-gate condition "Validated active public ticker" is satisfied by *any*
non-empty string. Today the only thing preventing a bogus TRADEABLE is the separate
`mapping_confidence >= 0.90` check. That is one guard doing two jobs; if bug #1 is
fixed in a way that ever yields a ≥0.90 confidence without a *validated* ticker,
this becomes a fail-open. The spec's precision policy wants fail-closed.

**Fix:** Set `active_public_ticker` from a real validation flag — i.e. the resolver
produced a `best_candidate` whose `is_active and is_public` is true and whose
`ticker` is a validated symbol — not merely `bool(ticker)`. Carry the resolved
`TickerCandidate` (or a boolean derived from it) into `ScoreContext` instead of the
raw name.

---

## 6. LOW — `_classify_sync` ignores the configured fallback model

**Where:** `src/gktrader/tasks/pipeline.py:201-204`.

```python
config = ClassifierConfig(
    api_key=settings.openrouter_api_key,
    model=settings.openrouter_model,
)   # fallback_model not passed
```

`settings.openrouter_fallback_model` (set in `.env` to
`google/gemini-2.5-flash-lite`) is never forwarded; the classifier uses the
`ClassifierConfig` default. It happens to equal the env value today, so behavior is
correct *by coincidence*. If the operator changes the env fallback, it is silently
ignored.

**Fix:** Pass `fallback_model=settings.openrouter_fallback_model` into
`ClassifierConfig`. Add a tiny test asserting the config reflects the setting.

---

## 7. LOW — Alpaca market status is always UNKNOWN; out-of-hours paper entry session is never resolved

**Where:** `src/gktrader/marketdata/alpaca.py:140-148`
(`_determine_market_status`) and `src/gktrader/tasks/pipeline.py:847-872` /
`964-985` (`create_alerts` paper-entry block).

The Alpaca `snapshot` endpoint response has **no `status` field** (live-confirmed
top-level keys: `dailyBar, latestQuote, latestTrade, minuteBar, prevDailyBar,
symbol`), so `_determine_market_status` always returns `MarketStatus.UNKNOWN`. As a
result the user-facing "market open/closed state" (§13 / §14) is never accurate,
and `create_alerts` persists the raw snapshot price as the paper entry without ever
calling `resolve_entry_session` (§13: "For out-of-hours alerts ... use the first
eligible regular-session price as paper entry."). Paper entries taken pre/post
market will be mispriced.

**Fix:** Determine market open/closed from a real source — e.g. `exchange_calendars`
(already a listed dependency) against the observation time, or Alpaca's
`/v2/clock` endpoint — and feed that into the snapshot. Then, in `create_alerts`,
when the market is closed/out-of-hours, use `resolve_entry_session` to pick the
first eligible regular-session entry price instead of the out-of-hours print.

---

## Reproduction notes (for whoever fixes these)

All findings were reproduced from the repo root with the project venv and the
committed `.env` loaded via `python-dotenv`. The two CRITICALs are the ones to fix
first and are each a one-spot wiring error in `tasks/jobs.py` / `sources/truthsocial.py`
rather than deep logic bugs — but they disable ticker validation and the primary
signal source respectively, so the live system would currently emit only
unvalidated REVIEW alerts (at best) and ingest no Truth Social posts.
