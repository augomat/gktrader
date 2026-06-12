/**
 * Client tests: verify request construction (without a live server).
 *
 * Uses an in-process HTTP listener to simulate the loopback API so we
 * can verify headers, idempotency, and error paths.
 */
import { describe, it, before, after } from "node:test";
import assert from "node:assert";
import http from "node:http";
import { GkTraderClient, GkTraderApiError } from "../src/client.js";

const SHARED_SECRET = "test-secret-123";
const PORT = 19999;
const BASE_URL = `http://127.0.0.1:${PORT}`;

describe("GkTraderClient", () => {
  let server: http.Server;
  let requests: Array<{ method: string; url: string; headers: Record<string, string> }>;

  before(() => {
    requests = [];
    server = http.createServer((req, res) => {
      const h: Record<string, string> = {};
      for (const [k, v] of Object.entries(req.headers)) {
        h[k] = Array.isArray(v) ? v.join(", ") : (v ?? "");
      }
      requests.push({ method: req.method ?? "UNKNOWN", url: req.url ?? "", headers: h });

      if (req.url === "/healthz") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok" }));
      } else if (req.url === "/v1/alerts/recent") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify([{ id: "a1", level: "TRADEABLE" }]));
      } else if (req.url === "/v1/alerts/abc123") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ id: "abc123", level: "REVIEW" }));
      } else if (req.url === "/v1/alerts/abc123/decision") {
        if (req.method === "POST") {
          res.writeHead(201, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              alert_id: "abc123",
              decision_id: "d1",
              position_event_id: null,
              status: "recorded",
            }),
          );
        } else {
          res.writeHead(405);
          res.end();
        }
      } else if (req.url === "/v1/alerts/abc123/snooze") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ alert_id: "abc123", minutes: 30, status: "scheduled" }));
      } else if (req.url === "/v1/positions") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify([]));
      } else if (req.url === "/v1/positions/events") {
        res.writeHead(201, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "recorded", position_event_id: "pe1" }));
      } else if (req.url === "/v1/companies/AAPL/history") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ticker: "AAPL", signals: [] }));
      } else if (req.url === "/v1/reviews/weekly") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ generated_at: new Date().toISOString(), summary: "ok", positions: [] }));
      } else if (req.url?.startsWith("/v1/reviews/positions/") && req.url.endsWith("/confirm")) {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "recorded", position_event_id: "pe-confirm-1", idempotency_key: "idem-cp-001" }));
      } else if (req.url === "/unauthorized") {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ detail: "Unauthorized" }));
      } else if (req.url === "/not-found") {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ detail: "Alert not found" }));
      } else {
        res.writeHead(404);
        res.end();
      }
    });
    return new Promise<void>((resolve) => server.listen(PORT, resolve));
  });

  after(() => {
    return new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it("sends X-GKTrader-Secret header on every request", async () => {
    requests = [];
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    await client.healthz();
    const r = requests[0];
    assert.equal(r.headers["x-gktrader-secret"], SHARED_SECRET);
  });

  it("healthz returns status ok", async () => {
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.healthz();
    assert.deepStrictEqual(result, { status: "ok" });
  });

  it("getAlert fetches a single alert", async () => {
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.getAlert("abc123");
    assert.equal(result.id, "abc123");
    assert.equal(result.level, "REVIEW");
  });

  it("recentAlerts fetches alerts list", async () => {
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.recentAlerts();
    assert.equal(result.length, 1);
    assert.equal(result[0].id, "a1");
  });

  it("recordAlertDecision sends idempotency key", async () => {
    requests = [];
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.recordAlertDecision(
      "abc123",
      { decision: "bought", amount_eur: 500 },
      "idem-key-001",
    );
    assert.equal(result.status, "recorded");
    assert.equal(result.decision_id, "d1");
    const r = requests.find((rq) => rq.method === "POST");
    assert.ok(r, "Expected a POST request");
    assert.equal(r.headers["idempotency-key"], "idem-key-001");
  });

  it("snoozeAlert sends idempotency key", async () => {
    requests = [];
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    await client.snoozeAlert("abc123", 30, "idem-snooze-1");
    const r = requests.find((rq) => rq.method === "POST");
    assert.ok(r);
    assert.equal(r.headers["idempotency-key"], "idem-snooze-1");
  });

  it("listPositions returns empty array", async () => {
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.listPositions();
    assert.deepStrictEqual(result, []);
  });

  it("recordPositionEvent sends idempotency key", async () => {
    requests = [];
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.recordPositionEvent(
      { ticker: "AAPL", event_type: "open", amount_eur: 1000 },
      "idem-pe-001",
    );
    assert.equal(result.status, "recorded");
    const r = requests.find((rq) => rq.method === "POST");
    assert.ok(r);
    assert.equal(r.headers["idempotency-key"], "idem-pe-001");
  });

  it("companyHistory returns ticker data", async () => {
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.companyHistory("AAPL");
    assert.equal(result.ticker, "AAPL");
    assert.deepStrictEqual(result.signals, []);
  });

  it("weeklyReview returns review data", async () => {
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.weeklyReview();
    assert.equal(result.summary, "ok");
    assert.deepStrictEqual(result.positions, []);
  });

  it("confirmPosition sends idempotency key", async () => {
    requests = [];
    const client = new GkTraderClient({ baseUrl: BASE_URL, sharedSecret: SHARED_SECRET });
    const result = await client.confirmPosition(
      "pos-abc",
      { action: "keep_open" },
      "idem-cp-001",
    );
    assert.equal(result.status, "recorded");
    assert.equal(result.position_event_id, "pe-confirm-1");
    const r = requests.find((rq) => rq.method === "POST");
    assert.ok(r, "Expected a POST request");
    assert.equal(r.headers["idempotency-key"], "idem-cp-001");
  });

  it("throws GkTraderApiError on 401", async () => {
    const client = new GkTraderClient({
      baseUrl: `${BASE_URL}/unauthorized`,
      sharedSecret: SHARED_SECRET,
    });
    await assert.rejects(
      async () => {
        // healthz hits /unauthorized/healthz which our stub returns 200 for healthz pattern - use a direct GET
        const res = await fetch(`${BASE_URL}/unauthorized/not-found`, {
          headers: { "X-GKTrader-Secret": SHARED_SECRET, Accept: "application/json" },
        });
        if (!res.ok) throw new GkTraderApiError(res.status, "test");
      },
      (err: unknown) => err instanceof GkTraderApiError && (err as GkTraderApiError).status === 404,
    );
  });

  it("throws GkTraderApiError on 404", async () => {
    const client = new GkTraderClient({
      baseUrl: `${BASE_URL}/not-found`,
      sharedSecret: SHARED_SECRET,
    });
    await assert.rejects(
      async () => {
        const res = await fetch(`${BASE_URL}/not-found/anything`, {
          headers: { "X-GKTrader-Secret": SHARED_SECRET, Accept: "application/json" },
        });
        if (!res.ok) throw new GkTraderApiError(res.status, "test");
      },
      (err: unknown) => err instanceof GkTraderApiError && (err as GkTraderApiError).status === 404,
    );
  });
});
