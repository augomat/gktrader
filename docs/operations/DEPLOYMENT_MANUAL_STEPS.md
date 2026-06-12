# GKTrader — Manual Deployment Steps & Remaining Work

Last updated: 2026-06-12.

This doc covers everything that is **not** application code an agent can fix in
the repo: host setup commands to run by hand, plus the integration/config work
still outstanding. Code bugs are tracked separately in `docs/BUGS_FOUND_ROUND2.md`.

---

## ✅ Already fixed in the repo (no action needed)

- **`psycopg` dependency** added to `pyproject.toml` (`psycopg[binary]>=3.2.0`).
  Without it every service crashed at import because the DB URL is
  `postgresql+psycopg://`.
- **Automatic migrations.** `compose.yaml` now has a one-shot `migrate` service
  (`alembic upgrade head`) that runs before `api`/`worker`/`scheduler`
  (`depends_on … condition: service_completed_successfully`). `migrations/env.py`
  now takes its URL from `GKTRADER_DATABASE_URL` (the app's source of truth), so
  the migration runs against the real Postgres inside the container network.
  Verified: a clean `alembic upgrade head` creates all 23 tables and stamps
  `0001_initial`.
- **Resolver + Truth Social criticals** (see `BUGS_FOUND_ROUND2.md` #1–#3).

---

## 1. Commands to run on the VPS first (host setup — do these by hand)

These are operator actions; agents cannot/should not run them.

### 1a. Install Docker Engine + Compose plugin
Docker is not in PATH on this VPS (plan §4). On Ubuntu/Debian:

```bash
# Official Docker convenience script (review it first if you prefer)
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh

# Let your user run docker without sudo (log out/in afterwards)
sudo usermod -aG docker "$USER"

# Verify
docker --version
docker compose version
```

### 1b. Confirm secrets file is present and locked down
`.env` already exists at the repo root and `compose.yaml` loads it via
`env_file:`. Make sure it is not world-readable and never committed:

```bash
cd /home/openclaw/gktrader
chmod 600 .env
git check-ignore .env   # should print ".env" (it is gitignored)
```

Before going live, replace the placeholder SEC contact in `.env`
(`GKTRADER_SEC_USER_AGENT=GKAgent/0.1 (ops@example.com)`) with a **real** email —
SEC fair-access can throttle/block fake contacts.

### 1c. Build and start the stack
From the repo root:

```bash
cd /home/openclaw/gktrader
docker compose build
docker compose up -d          # migrate runs first, then api/worker/scheduler
```

The `migrate` service runs once and exits 0; that is expected, not an error.

### 1d. Smoke-check it came up

```bash
docker compose ps                              # migrate = Exited(0); others Up
docker compose logs migrate                     # should show "Running upgrade -> 0001_initial"
curl -s http://127.0.0.1:8787/healthz           # API on loopback
docker compose logs -f worker                    # watch a poll cycle
```

`/readyz` requires the shared-secret header; `/healthz` does not.

### 1e. (Optional) Verify the deterministic resolver loaded
On the first poll the worker downloads the SEC ticker master (~10k rows) and
caches it. You should see no `SEC ticker master load failed` warnings in
`docker compose logs worker`.

---

## 2. Remaining application work for the cheap agent

Tracked in detail in `docs/BUGS_FOUND_ROUND2.md` (#4–#7). Summary + a couple of
items discovered during deployment review:

### 2a. Finish the Playwright wiring (in progress by the human)
- `_build_pipeline` (`tasks/jobs.py`) constructs `CommerceAdapter()` /
  `TruthSocialAdapter()` with **no** browser context. Inject a real
  `BrowserContext` (`CommerceAdapter(browser_context=ctx)`).
- **Create the context per worker *process*, lazily** — Celery prefork forks
  children and Playwright sync objects are not fork/thread-safe. Initialize in
  the `worker_process_init` signal (or lazily on first use under a lock), not at
  module import in the beat/parent. Use `launch_persistent_context(user_data_dir=…)`
  so the Truth Social session persists. Clean up via `atexit` **in the same
  process** that created it.
- Browser binaries are already installed by the Dockerfile
  (`playwright install chromium --with-deps`) — no manual step in Docker.
- **Scope to Commerce.** The Truth Social `_fetch_playwright` path is a stub
  (wrong URL: numeric account id instead of `@realDonaldTrump`; raw `innerText`
  heuristic in `_parse_text_listing`). Wiring a context fixes Commerce's 403,
  but TS-via-Playwright will still return junk. Truth Social already works via the
  fixed CNN mirror, so either rewrite the TS Playwright path properly or leave it
  disabled.

### 2b. `BUGS_FOUND_ROUND2.md` #5 — `active_public_ticker=bool(ticker)` fail-open
`pipeline.py` marks the TRADEABLE gate's "validated active public ticker" true for
any non-empty string. Derive it from the resolver's `best_candidate`
(`is_active and is_public` + validated symbol), not `bool(ticker)`.

### 2c. `BUGS_FOUND_ROUND2.md` #6 — classifier ignores configured fallback model
`_classify_sync` builds `ClassifierConfig` without
`fallback_model=settings.openrouter_fallback_model`. Pass it through.

### 2d. `BUGS_FOUND_ROUND2.md` #7 — market status always UNKNOWN
Alpaca's snapshot has no `status` field, so `_determine_market_status` always
returns UNKNOWN and out-of-hours paper entries are never session-resolved. Use
`exchange_calendars` (already a dependency) or Alpaca `/v2/clock`, then call
`resolve_entry_session` for out-of-hours entries.

### 2e. OpenClaw integration (Wave 3B — not yet validated)
The whole button / trade-follow-up / weekly-confirmation flow depends on this and
is **outside Docker** (OpenClaw runs on the host):
- Register the dedicated bot (`richy_trading_bot`) as a separate OpenClaw Telegram
  account; bind it to a dedicated `gktrader-agent`; allowlist only owner ID
  `324974555`; disable group access; minimal tool allowlist (no shell/browser/broker).
- Install/configure the `openclaw-gktrader` plugin (`integrations/openclaw-gktrader/`)
  to call the loopback API at `http://127.0.0.1:8787` with
  `GKTRADER_INTERNAL_API_SHARED_SECRET`.
- Confirm OpenClaw is the **only** process polling `getUpdates` for this token; the
  Python backend must only ever call `sendMessage` (it does).
- The contents/correctness of `integrations/openclaw-gktrader/` have **not** been
  validated yet — review before relying on it.

---

## 3. Operational notes / gotchas

- **First poll baselines, does not alert** (`GKTRADER_ENABLE_FIRST_START_BASELINE=true`).
  Expected — old feed items are stored, not alerted. To intentionally backfill with
  alerts, set `GKTRADER_ALLOW_ALERTS_DURING_REPLAY=true` (use with care).
- **`.env` is only auto-loaded by Compose** (`env_file:`). `Settings` has no
  `env_file`, so anything run *outside* Compose (e.g. ad-hoc `alembic` on the host)
  needs `GKTRADER_DATABASE_URL` exported manually.
- **Backups** (plan §16) are not automated here: add a nightly `pg_dump` cron with
  ≥14-day retention and document the restore once you launch.
- **Owner chat is already open** — a test message reached the owner, so Telegram
  delivery works as soon as the worker produces alerts.

---

## 4. Quick reference — full first-deploy sequence

```bash
# 0. (once) install docker — see §1a
# 1. configure secrets
cd /home/openclaw/gktrader
chmod 600 .env
# (edit .env: set a real SEC contact email)

# 2. build + launch (migrations run automatically)
docker compose build
docker compose up -d

# 3. verify
docker compose ps
curl -s http://127.0.0.1:8787/healthz
docker compose logs -f worker

# 4. then do the OpenClaw bot/plugin setup on the host (§2e)
```
