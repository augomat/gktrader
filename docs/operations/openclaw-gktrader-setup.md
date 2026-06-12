# GKTrader: Owner-Only Private Bot Setup

## Overview

The GKTrader dedicated Telegram bot is owner-only and must be configured
with a minimal tool allowlist. This document covers bot creation, OpenClaw
configuration, plugin loading, and security hardening.

## Prerequisites

- Running OpenClaw installation on the same VPS.
- Running GKTrader Docker Compose stack with the internal API on loopback.
- `GKTRADER_INTERNAL_API_SHARED_SECRET` set in `.env`.
- `GKTRADER_TELEGRAM_BOT_TOKEN` set in `.env` (the dedicated bot token).
- `GKTRADER_TELEGRAM_OWNER_ID` set in `.env` (your numeric Telegram user ID).

## 1. Create the Dedicated Telegram Bot

1. Open a chat with [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot` and follow the prompts.
   - Name: `GKTrader [your-suffix]`
   - Username: `gktrdr_yourname_bot` or similar.
3. Copy the token; set it as `GKTRADER_TELEGRAM_BOT_TOKEN` in `.env`.
4. Send `/setjoingroups` to BotFather.
   - Select the new bot.
   - Set to **Disable**. The GKTrader bot must never join groups.
5. Send `/setprivacy` to BotFather.
   - Select the new bot.
   - Set to **Enable** so it cannot read group messages where it is not a member.

## 2. Find Your Numeric Telegram User ID

1. Send any message to [@userinfobot](https://t.me/userinfobot).
2. Copy the numeric `id` field.
3. Set it as `GKTRADER_TELEGRAM_OWNER_ID` in `.env`.

## 3. Configure OpenClaw

### 3.1 Add the bot as a Telegram account

In your OpenClaw configuration, add the new bot token as a separate
Telegram account. The account must be bound to its own dedicated agent.

Example (refer to your OpenClaw config format):

```yaml
accounts:
  telegram:
    gktrader:
      token: "${GKTRADER_TELEGRAM_BOT_TOKEN}"
      agent: gktrader-agent
```

### 3.2 Create the dedicated agent

Create a `gktrader-agent` agent definition with these constraints:

```yaml
agents:
  gktrader-agent:
    model: "openai/gpt-4o"           # or your preferred conversational model
    system_prompt: |
      You are the GKTrader trading assistant. You help the owner review
      event-signal alerts, record trade decisions, manage positions, and
      review weekly performance.

      IMPORTANT RULES:
      - You may only call gktrader_* tools.
      - Never place or prepare real broker orders.
      - Never speculate about ticker mappings.
      - Ask for confirmation before recording any side effect.
    tools:
      allow:
        - gktrader_get_alert
        - gktrader_recent_alerts
        - gktrader_record_decision
        - gktrader_snooze_alert
        - gktrader_list_positions
        - gktrader_record_position_event
        - gktrader_company_history
        - gktrader_weekly_review
    allowlist:
      users:
        - "${GKTRADER_TELEGRAM_OWNER_ID}"
    # Explicitly deny dangerous tools
    tools:
      deny:
        - shell
        - browser
        - execute_command
        - network_request
```

### 3.3 Load the GKTrader plugin

```yaml
plugins:
  openclaw-gktrader:
    path: /home/openclaw/gktrader/integrations/openclaw-gktrader
    config:
      apiBaseUrl: "http://127.0.0.1:8000"
      sharedSecret: "${GKTRADER_INTERNAL_API_SHARED_SECRET}"
```

### 3.4 Build the plugin (if not already built)

```bash
cd /home/openclaw/gktrader/integrations/openclaw-gktrader
npm ci
npm run build
```

## 4. Verify the Setup

1. Start the GKTrader API: `docker compose up -d api`
2. Restart OpenClaw to pick up plugin and agent config changes.
3. Send `/start` to your dedicated bot from your owner Telegram account.
4. If the bot responds, the setup is working.
5. Ask: `Show recent alerts` — the agent should call `gktrader_recent_alerts`.

## 5. Security Hardening Checklist

- [ ] Bot cannot join groups (`/setjoingroups` → Disable).
- [ ] Bot privacy mode is enabled (`/setprivacy` → Enable).
- [ ] Only the owner's numeric user ID is in the allowlist.
- [ ] Agent tool allowlist contains only the 8 `gktrader_*` tools.
- [ ] Shell, browser, and broker tools are explicitly denied.
- [ ] Plugin `sharedSecret` matches the Python backend's value.
- [ ] The internal API is bound to loopback only (`127.0.0.1`).
- [ ] No second `getUpdates` poller is configured for this bot token.
- [ ] All side-effecting tools require idempotency keys (enforced by plugin).

## Troubleshooting

### Bot does not respond

1. Check OpenClaw logs for plugin load errors.
2. Verify `sharedSecret` matches between plugin config and `.env`.
3. Confirm the API is reachable: `curl -H "X-GKTrader-Secret: <secret>" http://127.0.0.1:8000/healthz`

### Plugin not loading

1. Run `npm run build` in the plugin directory.
2. Check that `dist/index.js` exists.
3. Verify the plugin path in OpenClaw config is absolute and correct.

### Unauthorized (401) on API calls

1. The `X-GKTrader-Secret` header must match `GKTRADER_INTERNAL_API_SHARED_SECRET`.
2. Check for trailing whitespace in the secret value.
