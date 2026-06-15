# Commerce gkfetch on Georg Laptop

Goal: run Commerce acquisition through Georg's Windows laptop because VPS/CM4 browser fingerprints currently fail the Commerce Cloudflare challenge.

GKTrader expects a gkfetch-compatible HTTP service on the laptop. The service drives a normal local Edge browser via CDP and returns rendered HTML to the VPS over the VPN.

## Network Shape

- VPS worker calls: `http://<georg-vpn-ip>:8899/fetch?url=https://www.commerce.gov/news/press-releases`
- Laptop gkfetch calls local Edge CDP: `http://127.0.0.1:9222`
- Edge loads Commerce directly from the laptop network.
- No SOCKS proxy is needed for this path.
- Do not expose Edge CDP `9222` on the VPN. Bind it to `127.0.0.1` only.

## Start Edge

Run this in PowerShell on the laptop:

```powershell
& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  --remote-debugging-address=127.0.0.1 `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:LOCALAPPDATA\Edge-RemoteDebug" `
  --no-first-run `
  "about:blank"
```

Important fingerprint requirements:

- Use real, visible Edge on Windows, not headless Chromium.
- Use a persistent profile directory.
- Verify WebGL is enabled and shows a real GPU renderer, not `SwiftShader` or `llvmpipe`.
- `navigator.webdriver` should be `false` when inspected in the page.

## gkfetch Service Contract

Expose an HTTP service on the laptop, port `8899`, reachable from the VPS over the VPN.

Endpoint:

```text
GET /fetch?url=<target-url>
X-Secret: <shared-secret>
```

Expected success response:

```json
{
  "html": "<html>...</html>",
  "status": 200,
  "title": "Press Releases | U.S. Department of Commerce",
  "url": "https://www.commerce.gov/news/press-releases"
}
```

Expected error response:

```json
{
  "error": "human-readable error"
}
```

The service should:

- Reject requests with a missing/wrong `X-Secret`.
- Only allow `https://www.commerce.gov/...` targets unless there is a deliberate reason to broaden scope.
- Connect to Edge via CDP at `http://127.0.0.1:9222`.
- Navigate to the requested URL.
- Wait until the Commerce page is actually loaded, not just until Cloudflare challenge HTML appears.
- Return the final `page.content()`, `page.title()`, final `page.url`, and document response status if available.

Suggested wait condition for Commerce listing:

```text
Wait until either:
- page HTML contains at least one `/news/press-releases/` link and title is not `Just a moment...`, or
- timeout expires and the current HTML/title/status are returned for diagnostics.
```

## Windows Firewall

Allow inbound TCP `8899` from the VPN/Tailscale interface or VPN subnet only.

Do not allow inbound TCP `9222`. Edge CDP must remain localhost-only.

## GKTrader VPS Env

Set these on the VPS `.env`:

```env
GKTRADER_GKFETCH_CM4_URL=http://100.88.46.68:8899
GKTRADER_GKFETCH_CM4_SECRET=...

GKTRADER_GKFETCH_GEORG_LAPTOP_URL=http://<georg-vpn-ip>:8899
GKTRADER_GKFETCH_GEORG_LAPTOP_SECRET=...
```

Commerce uses only `GKTRADER_GKFETCH_GEORG_LAPTOP_URL/SECRET`. It intentionally does not fall back to CM4/VPS because those endpoints are known to fail Commerce.

TruthSocial and other global browser-backed sources continue to use CM4/global gkfetch.

## Validation From VPS

Do not print the shared secret in logs.

Connectivity check:

```bash
curl -sS -H "X-Secret: $GKTRADER_GKFETCH_GEORG_LAPTOP_SECRET" \
  "$GKTRADER_GKFETCH_GEORG_LAPTOP_URL/health"
```

Commerce fetch check:

```bash
curl -sS -H "X-Secret: $GKTRADER_GKFETCH_GEORG_LAPTOP_SECRET" \
  "$GKTRADER_GKFETCH_GEORG_LAPTOP_URL/fetch?url=https%3A%2F%2Fwww.commerce.gov%2Fnews%2Fpress-releases"
```

Expected good signs:

- JSON `status` is `200`.
- JSON `title` is not `Just a moment...`.
- JSON `html` contains `/news/press-releases/` links.

Bad signs:

- `title` is `Just a moment...`.
- `status` is `403`.
- HTML text contains `Performing security verification`.
- HTML contains no `/news/press-releases/` links.

## Validation In GKTrader

After updating `.env`, restart worker/API code if containers are accessible:

```bash
./gkt-restart.sh
```

Run a live adapter check from the VPS project root:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
import os

for line in Path('.env').read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from gktrader.config.settings import Settings
from gktrader.tasks.jobs import _commerce_gkfetch_config
from gktrader.sources.commerce import CommerceAdapter

settings = Settings()
url, secret = _commerce_gkfetch_config(settings)
print('commerce_gkfetch_url_configured=', bool(url))
print('commerce_gkfetch_secret_configured=', bool(secret))

adapter = CommerceAdapter(gkfetch_url=url, gkfetch_secret=secret)
result = adapter.fetch_index()
print('fetch_path=', result.fetch_path)
print('items=', len(result.items))
print('first_title=', result.items[0].title if result.items else '')
print('first_url=', result.items[0].detail_url if result.items else '')
PY
```

Expected:

```text
commerce_gkfetch_url_configured= True
commerce_gkfetch_secret_configured= True
fetch_path= playwright
items= <non-zero>
```

If it still fails with `Commerce gkfetch fetch returned Cloudflare challenge; status=403; title='Just a moment...'`, the laptop-side Edge session is still not passing Commerce's challenge.
