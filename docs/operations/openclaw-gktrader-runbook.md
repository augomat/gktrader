# GKTrader OpenClaw Plugin: Operations Runbook

## Service Relationships

```
┌─────────────────────────────────────────┐
│  Telegram Bot API                        │
│    ↑ sendMessage                         │
│    │ (Python backend renders & sends)    │
│    │                                     │
│  ┌─OpenClaw───┐    ┌─GKTrader API─────┐ │
│  │ Telegram   │    │ FastAPI :8000     │ │
│  │ Poller     │    │ (loopback only)   │ │
│  │   ↓        │    │   ↑               │ │
│  │ GKTrader   │───→│   │ shared-secret │ │
│  │ Agent      │    │   │               │ │
│  │   ↓        │    │   ↓               │ │
│  │ Plugin     │    │ PostgreSQL :5432  │ │
│  └────────────┘    │ Redis :6379       │ │
│                     └───────────────────┘ │
└─────────────────────────────────────────┘
```

- **Inbound messages**: OpenClaw Telegram poller (exclusive).
- **Outbound messages**: Python backend renders and dispatches; OpenClaw handles button callbacks and natural-language commands.
- **Plugin**: Read-only access to alerts/positions/reviews; validated mutation through idempotent endpoints.
- **Broker**: None. The plugin cannot access any broker.

## Plugin Tool Inventory

| # | Tool | HTTP Method | Side Effect | Idempotency Key |
|---|---|---|---|---|
| 1 | `gktrader_get_alert` | GET | No | — |
| 2 | `gktrader_recent_alerts` | GET | No | — |
| 3 | `gktrader_record_decision` | POST | Yes | Required |
| 4 | `gktrader_snooze_alert` | POST | Yes | Required |
| 5 | `gktrader_list_positions` | GET | No | — |
| 6 | `gktrader_record_position_event` | POST | Yes | Required |
| 7 | `gktrader_company_history` | GET | No | — |
| 8 | `gktrader_weekly_review` | GET | No | — |

## Health Checks

### Plugin health

The plugin is stateless. If it cannot reach the API, tool calls will fail
with a `GkTraderApiError`. Check:

```bash
# From the VPS
curl -s http://127.0.0.1:8000/healthz
# → {"status":"ok"}

curl -s -H "X-GKTrader-Secret: ${GKTRADER_INTERNAL_API_SHARED_SECRET}" \
  http://127.0.0.1:8000/readyz
# → {"status":"ready"}
```

### Plugin build health

The plugin must be rebuilt after any source change:

```bash
cd /home/openclaw/gktrader/integrations/openclaw-gktrader
npm run build
```

## Common Operations

### Restart after config change

```bash
# 1. Rebuild plugin if source changed
cd /home/openclaw/gktrader/integrations/openclaw-gktrader && npm run build

# 2. Restart OpenClaw (method depends on your setup)
#    e.g. systemctl restart openclaw, pm2 restart openclaw, etc.
```

### Update plugin config

Edit OpenClaw's agent config to change `apiBaseUrl` or `sharedSecret`,
then restart OpenClaw. No rebuild is needed for config-only changes.

### Verify tool availability

From the owner's dedicated Telegram chat, send:
- `Show recent alerts` — should call `gktrader_recent_alerts`
- `Show open positions` — should call `gktrader_list_positions`

### Check API reachability from plugin

The plugin uses Node.js `fetch`. Verify DNS and loopback:

```bash
node -e "
  fetch('http://127.0.0.1:8000/healthz', {
    headers: {
      'X-GKTrader-Secret': process.env.GKTRADER_INTERNAL_API_SHARED_SECRET,
      'Accept': 'application/json'
    }
  }).then(r => r.json()).then(console.log)
"
```

## Monitoring

### What to watch

1. **API healthz** — if `/healthz` returns non-200, the API is down.
2. **Dedicated bot responsiveness** — send a `/start` from owner account daily.
3. **Plugin load errors** — check OpenClaw startup logs for plugin-related errors.
4. **Tool call failures** — OpenClaw agent logs will show `GkTraderApiError` for API issues.

### Log locations

- OpenClaw logs: depends on your OpenClaw configuration.
- GKTrader API logs: Docker Compose logs (`docker compose logs api`).
- Plugin: the plugin itself does not produce logs; all diagnostics come from OpenClaw's tool execution logging.

## Idempotency

All side-effecting tools (`record_decision`, `snooze_alert`, `record_position_event`)
require an `idempotency_key` parameter. The key is sent as the `Idempotency-Key`
HTTP header to the API.

The LLM agent calling these tools **must** provide a unique idempotency key.
The Python backend uses the key to:
- Detect duplicate requests.
- Return the previously recorded result without creating a new side effect.

The agent should generate keys using a deterministic pattern, such as:
`gkt-{tool_name}-{alert_id}-{decision}`.

## Recovery Procedures

### API returns 401 (Unauthorized)

1. Verify `GKTRADER_INTERNAL_API_SHARED_SECRET` in `.env` matches the plugin config.
2. Check that the `X-GKTrader-Secret` header is being sent (see client.test.ts for reference).
3. Restart the API: `docker compose restart api`.

### Plugin fails to load

1. Run `npm run build` and check for TypeScript errors.
2. Verify `dist/index.js` and `dist/index.d.ts` exist.
3. Check OpenClaw startup logs for import errors.
4. Verify Node.js version >= 20.

### Bot is unresponsive

1. Check that the dedicated bot token has not been revoked.
2. Verify the owner's numeric user ID is correct.
3. Check OpenClaw Telegram account configuration.
4. Ensure no other process is calling `getUpdates` for this token.

## Security Contacts

All security concerns should be reported to the VPS operator.
The plugin contains no credentials in its source; all secrets come from
environment variables through OpenClaw's config system.
