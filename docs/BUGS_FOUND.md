# GKTrader — Implementation Bugs vs. IMPLEMENTATION_PLAN.md

Validation of the current `src/gktrader` implementation against `IMPLEMENTATION_PLAN.md`
(the spec) and against the product idea (a conservative, auditable, anti-spam event-signal
system that records trades and measures paper performance).

**Status when reviewed:** all 430 tests pass. Every bug below is in a gap the tests do not
cover. Fix them in roughly the order listed (top = most damaging). Each item gives the file,
the line area, what is wrong, what the spec says, and a concrete fix.

When you fix one, add or extend a test that would have caught it.

---

## 1. CRITICAL — Recording a trade from an alert always fails with HTTP 422

**Where:** `src/gktrader/api/services.py:90` (`record_alert_decision`)
and `src/gktrader/domain/contracts.py:136` (`AlertPayload`).

**What happens:** `record_alert_decision` does:

```python
rendered = alert.rendered_payload or {}
ticker = rendered.get("ticker")
if not ticker:
    raise HTTPException(422, "Alert is missing ticker context")
```

But `AlertPayload` (what gets stored in `alert.rendered_payload`) has **no `ticker` field** —
it only has `alert_id, level, text, continuation_messages, buttons, dedupe_key`. So
`rendered.get("ticker")` is always `None`, and **every** `Bought` / `Shorted` /
`Sold/Reduced` decision raises 422.

**Spec impact:** Breaks §14 "Trade Follow-Up Flow" and end-to-end golden scenario #8
("Telegram button callback and amount follow-up update the position ledger"). The core
"record whether a trade was made" feature is non-functional.

**Fix (pick one):**
- Preferred: add `ticker: str` and `company: str` to `AlertPayload`, populate them in
  `render_alert_payload` (the renderer already has `context.ticker` /
  `context.company_name`), and they will flow into `rendered_payload`. Then
  `rendered.get("ticker")` works.
- Or: in `record_alert_decision`, look the ticker up from the linked
  `SignalEvent.payload["ticker"]` (the pipeline already stores it there at
  `tasks/pipeline.py` when building `signal.payload`) via `alert.signal_event_id`.

Add a test that posts a `Bought` decision and asserts a `PositionEvent` is created.

---

## 2. CRITICAL — Pipeline crashes (and rolls back the whole poll) when a known event reappears after its cooldown

**Where:** `src/gktrader/tasks/pipeline.py:636-689` (`create_signals`) together with
`src/gktrader/db/models.py:176` (`fingerprint ... unique=True`).

**What happens:**

```python
existing_sig = self.db.query(SignalEvent).filter_by(fingerprint=fingerprint).first()
if existing_sig is not None:
    ...
    if cooldown_state.remaining_seconds > 0:
        # skip (still on cooldown)
        continue
    # else: FALLS THROUGH

signal = SignalEvent(id=_uuid(), fingerprint=fingerprint, ...)  # same fingerprint!
self.db.add(signal)
self.db.flush()   # IntegrityError: duplicate unique fingerprint
```

When the same canonical event is seen again **after** the 6h cooldown expires, the code falls
through and inserts a second `SignalEvent` with the same `fingerprint`, which is a UNIQUE
column. The flush raises `IntegrityError`, and `jobs.py::_run_in_session` rolls back the
**entire** poll cycle. So re-alerting after a cooldown is impossible, and the failure is
destructive (loses that whole poll's work).

**Spec impact:** §12 cooldown + "Confirmations should attach to the canonical event"; DoD
"alerts are ... cooldown-aware". Restart/idempotency guarantees are also undermined because a
recurring fingerprint poisons every future poll.

**Fix:** When `existing_sig` is found and the cooldown has expired, do **not** insert a new
row. Instead either (a) re-use the existing canonical `SignalEvent` and create a fresh
`Alert`/outbox row against it (alerts are per-delivery; the canonical event is per-catalyst),
updating `existing_sig.created_at`/last-alerted timestamp; or (b) attach the new evidence to
the existing event. Only create a brand-new `SignalEvent` when the fingerprint is genuinely
new. See bug #3/#4 for how cooldown and material-update should actually drive this.

---

## 3. HIGH — Cooldown is keyed by fingerprint, not by (ticker, event_type, direction)

**Where:** `src/gktrader/tasks/pipeline.py:636-661` (`create_signals`).

**What the spec says (§12):** "Apply a six-hour cooldown per `(ticker, event_type,
direction)`."

**What the code does:** It only finds a prior event by exact `fingerprint` match, then checks
the time. The fingerprint also includes amount, award/contract IDs, and the published-date
bucket. So two genuinely-similar events for the **same** `(ticker, event_type, direction)` but
with a different amount, a different award ID, or simply seen on a different calendar day get
**different fingerprints** → no cooldown is applied → both are delivered. The anti-spam
cooldown effectively never fires except for byte-identical repeats. Note the code even
constructs a `CooldownKey(ticker, event_type, direction)` at line ~641 but never uses it for
the lookup.

**Fix:** Maintain cooldown state keyed by `(ticker, event_type, direction)` (e.g. a
`cooldown_state` table/row or Redis key, or query the most recent delivered `SignalEvent` for
that ticker+type+direction). On a new event for that key within 6h, suppress unless it is a
material update (bug #4). Use `fingerprint` only for exact-duplicate detection, not for the
cooldown window.

---

## 4. HIGH — Material-update override is implemented but never wired in

**Where:** `src/gktrader/intelligence/cooldown.py:78` (`is_material_update`) — defined and
unit-tested, but `grep` shows it is **never imported or called** by the pipeline.

**What the spec says (§12):** A materially-new event overrides the cooldown when direction
changes, action status changes, a new official source confirms it, a new amount/award/contract
ID appears, the score/level increases, or a revised source adds materially different evidence.

**What the code does:** `create_signals` only checks elapsed time. A same-key event inside the
6h window is always suppressed, even if it is a material escalation (e.g. "proposed" →
"awarded" → "cancelled", or a second official source confirming).

**Fix:** In the cooldown branch (after fix #3), when within the window, call
`is_material_update(previous_event, new_event)` and, if material, proceed to deliver (and
attach to the canonical event). Build the `previous_event` / `new_event` dicts from the stored
signal payload + the new extracted payload.

**Also note:** `cooldown.py:156 _level_rank` ranks `AVOID_CHASE` (4) **above** `TRADEABLE`
(3). A `TRADEABLE → AVOID_CHASE` transition (a price-driven *downgrade*) would then count as a
level "increase" and wrongly trigger a material update. Give AVOID_CHASE a rank at/below
TRADEABLE, or exclude AVOID_CHASE from the "level increased" check.

---

## 5. HIGH — Paper trades and performance snapshots are never created

**Where:** `src/gktrader/reporting/paper.py` (`make_paper_entry`) and
`src/gktrader/reporting/horizons.py` are fully implemented and unit-tested, but `grep` shows
**no code ever constructs a `PaperTrade` or `PerformanceSnapshot` row**, and no Celery task
computes horizons.

**What the spec says:** §13 + Milestone M4 + DoD: every actionable alert opens a paper entry
(REVIEW €500, TRADEABLE €1,000, others €0), and 1h/1d/5d/20d performance horizons are measured
on trading sessions. The weekly report groups these results.

**What the code does:** `generate_weekly_review` (`tasks/jobs.py:164`) queries `PaperTrade` and
`PerformanceSnapshot`, but those tables are always empty, so the weekly report always shows
zero trades and no returns. The whole paper-performance subsystem is dead.

**Fix:**
- In `create_alerts` (after the alert is created and snapshot taken), call `make_paper_entry`
  and persist a `PaperTrade` row (use `resolve_entry_session` for out-of-hours entries per
  §13).
- Add a scheduled Celery task (Celery Beat) that, for due paper trades, computes each horizon
  with `compute_horizon_session` + an Alpaca price lookup and writes `PerformanceSnapshot`
  rows (return, max drawdown, max runup, missing-data/quality flags).

---

## 6. MEDIUM — First-start baseline and replay guard are configured but unimplemented

**Where:** `src/gktrader/config/settings.py:25-26`
(`allow_alerts_during_replay`, `enable_first_start_baseline`) — defined but `grep` shows
neither is ever read.

**What the spec says (§9):** "On first startup, baseline currently available feed items
without sending Telegram alerts." and "Allow alerts during replay/backfill only through an
explicit administrative flag."

**Risk:** On real deployment, the very first poll will classify every currently-available feed
item and can emit a burst of alerts for old news — exactly the spam the design is trying to
avoid.

**Fix:** On first successful poll of a source (no prior `source_cursor`), ingest + store raw
documents but mark the resulting signals as baseline (suppress delivery / force WATCH) unless
`allow_alerts_during_replay` is set. Gate the delivery stage on these flags.

---

## 7. MEDIUM — "Stale (>24h) → internal-only" is never applied

**Where:** scoring supports it (`scoring.py:59 is_stale`, plus the −1 modifier and the
TRADEABLE-gate exclusion), but `create_signals` (`tasks/pipeline.py:595-604`) builds
`ScoreContext` **without ever setting `is_stale`**, and nothing computes staleness from
`published_at`.

**What the spec says (§9):** "Treat first-seen documents older than 24 hours as stale and
internal-only by default." (and §11 −1 stale modifier, and the TRADEABLE gate "Event is not
stale or recycled").

**Fix:** In `create_signals`, compute `is_stale = (detected_at - published_at) > 24h` (when
`published_at` is known) and pass it into `ScoreContext`. Stale first-seen docs should land at
WATCH (internal only) by default.

---

## 8. MEDIUM — Classifier uses plain JSON mode, not strict Structured Outputs, and has no fallback model

**Where:** `src/gktrader/intelligence/classifier.py:109`.

**What the code does:** `response_format={"type": "json_object"}` — that is OpenRouter/OpenAI
"JSON mode", which does **not** enforce the schema. The spec (§7 "strict JSON matching a
versioned schema", §10 "strict Structured Outputs", confirmed decision "OpenRouter with strict
structured output") wants `{"type": "json_schema", "json_schema": {... ClassifierResult
schema ..., "strict": true}}`. Current behaviour relies entirely on post-hoc Pydantic
validation + one repair retry (fail-closed, which is good) but skips provider-side enforcement.

Also: §10 specifies a **fallback model** (`Fallback: google/gemini-2.5-flash-lite`). There is
no fallback-model path — on an HTTP error the run just returns `FAILED`.

**Fix:** Send a real json_schema structured-output request (derive the schema from
`ClassifierResult.model_json_schema()`), and add a fallback-model attempt before failing
closed. Keep the existing repair/fail-closed behaviour as the final guard.

---

## 9. LOW/MEDIUM — Fetch-path-delay scoring penalty never applied (Truth Social mirror latency)

**Where:** `src/gktrader/intelligence/scoring.py:83-118` (`compute_modifiers`).

**What the spec says (§11):** "−2: Source is secondary-only **or fetch path has material
delay**." Also §4 / golden #7: CNN-mirror Truth Social posts must reflect the fallback latency
in scoring/alerts.

**What the code does:** `compute_modifiers` only checks `is_secondary_source`. The
`fetch_path_has_delay` field exists on `ScoreContext` but is never read, and the pipeline never
sets either flag based on `fetch_path` (e.g. `cnn_mirror`).

**Fix:** In `compute_modifiers`, also apply the −2 when `fetch_path_has_delay`. In
`create_signals`, set `is_secondary_source` / `fetch_path_has_delay` from the raw document's
`fetch_path` (e.g. `cnn_mirror`, `playwright`).

---

## 10. LOW — Dead / incorrect lookup in `create_signals`

**Where:** `src/gktrader/tasks/pipeline.py:547-553`.

```python
existing_signal = {
    ee.id: self.db.query(SignalEvent).filter(SignalEvent.id == ee.id).first()
    for ee in all_extracted
}
```

This matches `SignalEvent.id == ExtractedEvent.id`, but signal IDs are freshly generated UUIDs
that never equal an extracted-event ID, so every value is `None`, and the dict is never used
afterward. Dead code that signals confused intent — remove it (the real "already produced a
signal?" guard should be part of the fix for #2/#3).

---

## 11. LOW — Resolver claims to strip corporate suffixes but doesn't

**Where:** `src/gktrader/intelligence/resolver.py:238-250` (`_normalize_name`).

The docstring says it "Removes common corporate suffixes for comparison purposes," but the
body only lowercases, strips, and collapses whitespace — no suffix removal. So "Example
Corporation" will not exact-match an SEC legal name of "Example Corp", pushing it into fuzzy
matching unnecessarily. This is conservative (fails closed, which the precision policy likes)
but contradicts the documented behaviour and lowers recall.

**Fix:** Either implement conservative suffix normalisation (Inc/Corp/Co/Ltd/LLC/PLC/etc.) for
the **comparison** key while keeping the original for display, or correct the docstring to
match reality. Whichever you choose, add a resolver test for the "Corporation" vs "Corp" case.

---

## 12. LOW — Weekly review sends no inline confirmation buttons

**Where:** `src/gktrader/tasks/jobs.py:233` (`deliver_weekly_review`).

**Spec (§14):** the weekly position-confirmation message should offer `Keep open`, `Close`,
`Adjust` buttons. The current message is plain text telling the user to "use the confirmation
tools". The API + interaction-state plumbing exists; only the inline keyboard is missing.

**Fix:** Attach an inline keyboard per open position (callback payloads must stay < 64 bytes,
e.g. `gkt:p:<short-id>:keep`), consistent with the alert button scheme in
`src/gktrader/alerts/keyboard.py`. (Lower priority if OpenClaw is expected to drive
confirmation conversationally, but it is a stated spec requirement.)

---

## Things that are correct (so you don't "fix" them)

- Base catalyst scores by event type (`scoring.py:15-28`) match the §11 table.
- Price-move downgrade thresholds (<10% retain, 10–25% REVIEW, >25% AVOID_CHASE) are
  consistent between `scoring.py` and `marketdata/downgrade.py`, and AVOID_CHASE is still sent
  (golden #5).
- Market data can only downgrade, never promote (`apply_market_downgrade` only acts on
  TRADEABLE).
- Paper notionals (`paper.py:25-31`) match the §13 table exactly.
- WATCH is never delivered: `create_alerts` skips it and `render_alert_payload` raises on it.
- At-most-once delivery: `deliver_pending` never re-picks `UNKNOWN` outbox rows (golden #11).
- Telegram messages are sent as plain text (no `parse_mode` for alerts), so the unused
  `_escape_markdown_v2` is harmless — but delete it or wire it up to avoid confusion.
- Weekly review crontab is `Sunday 14:00` in `Europe/Vienna` with `enable_utc=True`, which is
  DST-safe (§14).
- Bearish alerts include prior bullish history with full continuation messages (renderer +
  `continuation.py`); note `history.py::collect_bullish_history` (with its 50-item cap) is NOT
  the path the pipeline uses, so there is no silent truncation in practice.
