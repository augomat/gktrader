# openclaw-gktrader

Restricted GKTrader event-signal tool plugin for OpenClaw.  
Provides 8 tools that call **only** the GKTrader loopback FastAPI internal service.

## Safety

- No shell, browser, broker, or order-execution tools.
- All side-effecting tools require idempotency keys.
- All requests include the `X-GKTrader-Secret` shared-secret authorization header.
- The API is bound to loopback only (`127.0.0.1`).

## Tools

| Tool | HTTP | Description |
|---|---|---|
| `gktrader_get_alert` | GET `/v1/alerts/{id}` | Fetch a single alert by ID |
| `gktrader_recent_alerts` | GET `/v1/alerts/recent` | List recent alerts (up to 50) |
| `gktrader_record_decision` | POST `/v1/alerts/{id}/decision` | Record trade decision (idempotent) |
| `gktrader_snooze_alert` | POST `/v1/alerts/{id}/snooze` | Snooze alert for N minutes (idempotent) |
| `gktrader_list_positions` | GET `/v1/positions` | List projected positions |
| `gktrader_record_position_event` | POST `/v1/positions/events` | Record manual position event (idempotent) |
| `gktrader_company_history` | GET `/v1/companies/{ticker}/history` | Prior bullish signals for a ticker |
| `gktrader_weekly_review` | GET `/v1/reviews/weekly` | Weekly review with open positions |

## Configuration

The plugin requires two config values in OpenClaw's plugin settings:

| Key | Description |
|---|---|
| `apiBaseUrl` | Loopback API base URL, default `http://127.0.0.1:8000` |
| `sharedSecret` | Must match `GKTRADER_INTERNAL_API_SHARED_SECRET` in the Python backend |

These are defined in the plugin config schema and passed by OpenClaw.

## Installation

### Prerequisites

- Node.js >= 20
- OpenClaw running on the same host
- GKTrader FastAPI service running on loopback
- `GKTRADER_INTERNAL_API_SHARED_SECRET` set in the Python `.env`

### Build

```bash
cd integrations/openclaw-gktrader
npm ci
npm run build
```

### Load into OpenClaw

Add the plugin path to your OpenClaw agent configuration. Example:

```yaml
# In your OpenClaw agent config for the dedicated GKTrader agent:
plugins:
  openclaw-gktrader:
    path: /home/openclaw/gktrader/integrations/openclaw-gktrader
    config:
      apiBaseUrl: "http://127.0.0.1:8000"
      sharedSecret: "${GKTRADER_INTERNAL_API_SHARED_SECRET}"
```

**Important constraints:**

- Only the owner's numeric Telegram user ID may be allowlisted.
- The agent must not receive shell, browser, broker, or general network tools.
- The agent must not be added to groups.
- Use the dedicated bot token (not an existing multi-purpose bot).

## Testing

```bash
npm test
```

## Architecture

```
Telegram User / Buttons
  → OpenClaw Telegram Poller (exclusive inbound)
  → Dedicated GKTrader Agent
  → This Restricted Plugin
  → Loopback FastAPI (127.0.0.1:8000)
  → PostgreSQL / Redis
```

The plugin never holds authoritative trading state in memory.  
All state is in PostgreSQL, accessed through the validated internal API.
