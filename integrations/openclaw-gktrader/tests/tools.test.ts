/**
 * Tool schema and plugin entry tests.
 *
 * Verify that:
 *  - All 9 tool names match the approved contract.
 *  - Side-effecting tools require idempotency_key.
 *  - The plugin entry exports the correct shape.
 *  - Parameter schemas validate correctly.
 */
import { describe, it } from "node:test";
import assert from "node:assert";
import { Type } from "typebox";
import { Value } from "typebox/value";

// Import the main plugin entry (default export)
import plugin from "../src/index.js";
import {
  RecordDecisionParams,
  SnoozeAlertParams,
  RecordPositionEventParams,
  GetAlertParams,
  RecentAlertsParams,
  ListPositionsParams,
  CompanyHistoryParams,
  WeeklyReviewParams,
  ConfirmPositionParams,
} from "../src/schemas.js";

describe("Plugin entry shape", () => {
  it("has correct id", () => {
    assert.equal(plugin.id, "openclaw-gktrader");
  });

  it("has name and description", () => {
    assert.ok(plugin.name.length > 0);
    assert.ok(plugin.description.length > 0);
  });

  it("exports a configSchema with apiBaseUrl and sharedSecret", () => {
    const schema = plugin.configSchema as Record<string, unknown>;
    assert.ok(schema.properties);
    const props = schema.properties as Record<string, unknown>;
    assert.ok("apiBaseUrl" in props);
    assert.ok("sharedSecret" in props);
  });

  it("has a register function", () => {
    assert.equal(typeof plugin.register, "function");
  });

  it("register calls registerTool for 9 tools", () => {
    const registered: string[] = [];
    const fakeApi = {
      pluginConfig: { apiBaseUrl: "http://127.0.0.1:9999", sharedSecret: "test" },
      registerTool(tool: { name: string }, _opts?: unknown) {
        registered.push(tool.name);
      },
    };
    plugin.register(fakeApi);
    assert.equal(registered.length, 9, `Expected 9 tools, got ${registered.length}: ${registered.join(", ")}`);
  });
});

describe("Tool name contract", () => {
  it("matches the 9 approved tool names", () => {
    const registered: string[] = [];
    const fakeApi = {
      pluginConfig: { apiBaseUrl: "http://x", sharedSecret: "x" },
      registerTool(tool: { name: string }, _opts?: unknown) {
        registered.push(tool.name);
      },
    };
    plugin.register(fakeApi);

    const expected = [
      "gktrader_get_alert",
      "gktrader_recent_alerts",
      "gktrader_record_decision",
      "gktrader_snooze_alert",
      "gktrader_list_positions",
      "gktrader_record_position_event",
      "gktrader_company_history",
      "gktrader_weekly_review",
      "gktrader_confirm_position",
    ];
    assert.deepStrictEqual(registered.sort(), expected.sort());
  });
});

describe("Parameter schema validation", () => {
  it("GetAlertParams requires alert_id", () => {
    assert.ok(
      Value.Check(GetAlertParams, { alert_id: "abc-123" }),
      "Valid alert_id should pass",
    );
    assert.equal(
      Value.Check(GetAlertParams, {}),
      false,
      "Missing alert_id should fail",
    );
  });

  it("RecordDecisionParams requires idempotency_key", () => {
    assert.ok(
      Value.Check(RecordDecisionParams, {
        alert_id: "a1",
        decision: "bought",
        idempotency_key: "k1",
      }),
      "Valid record_decision should pass",
    );
    assert.equal(
      Value.Check(RecordDecisionParams, { alert_id: "a1", decision: "bought" }),
      false,
      "Missing idempotency_key should fail",
    );
  });

  it("RecordDecisionParams validates decision enum", () => {
    assert.ok(
      Value.Check(RecordDecisionParams, {
        alert_id: "a1",
        decision: "no_trade",
        idempotency_key: "k1",
      }),
      "no_trade is valid",
    );
    assert.equal(
      Value.Check(RecordDecisionParams, {
        alert_id: "a1",
        decision: "invalid",
        idempotency_key: "k1",
      }),
      false,
      "Invalid decision should fail",
    );
  });

  it("SnoozeAlertParams requires idempotency_key", () => {
    assert.ok(
      Value.Check(SnoozeAlertParams, {
        alert_id: "a1",
        minutes: 30,
        idempotency_key: "k1",
      }),
    );
    assert.equal(
      Value.Check(SnoozeAlertParams, {
        alert_id: "a1",
        minutes: 30,
      }),
      false,
      "Missing idempotency_key should fail",
    );
  });

  it("SnoozeAlertParams validates minutes range", () => {
    assert.ok(
      Value.Check(SnoozeAlertParams, {
        alert_id: "a1",
        minutes: 1,
        idempotency_key: "k1",
      }),
      "minutes=1 is valid",
    );
    assert.ok(
      Value.Check(SnoozeAlertParams, {
        alert_id: "a1",
        minutes: 1440,
        idempotency_key: "k1",
      }),
      "minutes=1440 is valid",
    );
    assert.equal(
      Value.Check(SnoozeAlertParams, {
        alert_id: "a1",
        minutes: 0,
        idempotency_key: "k1",
      }),
      false,
      "minutes=0 should fail",
    );
    assert.equal(
      Value.Check(SnoozeAlertParams, {
        alert_id: "a1",
        minutes: 1441,
        idempotency_key: "k1",
      }),
      false,
      "minutes=1441 should fail",
    );
  });

  it("RecordPositionEventParams requires idempotency_key", () => {
    assert.ok(
      Value.Check(RecordPositionEventParams, {
        ticker: "AAPL",
        event_type: "open",
        amount_eur: 1000,
        idempotency_key: "k1",
      }),
    );
    assert.equal(
      Value.Check(RecordPositionEventParams, {
        ticker: "AAPL",
        event_type: "open",
        amount_eur: 1000,
      }),
      false,
      "Missing idempotency_key should fail",
    );
  });

  it("RecordPositionEventParams validates event_type enum", () => {
    for (const evt of ["open", "increase", "reduce", "close", "confirm", "adjust"]) {
      assert.ok(
        Value.Check(RecordPositionEventParams, {
          ticker: "AAPL",
          event_type: evt,
          amount_eur: 500,
          idempotency_key: "k1",
        }),
        `${evt} should be valid`,
      );
    }
    assert.equal(
      Value.Check(RecordPositionEventParams, {
        ticker: "AAPL",
        event_type: "invalid_type",
        amount_eur: 500,
        idempotency_key: "k1",
      }),
      false,
      "Invalid event_type should fail",
    );
  });

  it("RecordPositionEventParams validates ticker pattern", () => {
    assert.ok(
      Value.Check(RecordPositionEventParams, {
        ticker: "AAPL",
        event_type: "open",
        amount_eur: 500,
        idempotency_key: "k1",
      }),
      "AAPL is valid",
    );
    assert.ok(
      Value.Check(RecordPositionEventParams, {
        ticker: "BRK.B",
        event_type: "open",
        amount_eur: 500,
        idempotency_key: "k1",
      }),
      "BRK.B is valid (dot allowed)",
    );
  });

  it("CompanyHistoryParams requires ticker", () => {
    assert.ok(Value.Check(CompanyHistoryParams, { ticker: "AAPL" }));
    assert.equal(Value.Check(CompanyHistoryParams, {}), false);
  });

  it("Empty-object schemas accept empty objects", () => {
    assert.ok(Value.Check(ListPositionsParams, {}));
    assert.ok(Value.Check(WeeklyReviewParams, {}));
  });

  it("RecentAlertsParams has optional limit with bounds", () => {
    assert.ok(Value.Check(RecentAlertsParams, {}), "Empty is valid (limit defaults)");
    assert.ok(
      Value.Check(RecentAlertsParams, { limit: 10 }),
      "limit=10 is valid",
    );
    assert.equal(
      Value.Check(RecentAlertsParams, { limit: 0 }),
      false,
      "limit=0 should fail",
    );
    assert.equal(
      Value.Check(RecentAlertsParams, { limit: 51 }),
      false,
      "limit=51 should fail",
    );
  });

  it("ConfirmPositionParams requires position_id, action, idempotency_key", () => {
    assert.ok(
      Value.Check(ConfirmPositionParams, {
        position_id: "p1",
        action: "keep_open",
        idempotency_key: "k1",
      }),
      "Valid confirm should pass",
    );
    assert.equal(
      Value.Check(ConfirmPositionParams, {
        position_id: "p1",
        action: "keep_open",
      }),
      false,
      "Missing idempotency_key should fail",
    );
    assert.equal(
      Value.Check(ConfirmPositionParams, {
        action: "keep_open",
        idempotency_key: "k1",
      }),
      false,
      "Missing position_id should fail",
    );
  });

  it("ConfirmPositionParams validates action enum", () => {
    for (const action of ["keep_open", "close", "adjust"]) {
      assert.ok(
        Value.Check(ConfirmPositionParams, {
          position_id: "p1",
          action,
          idempotency_key: "k1",
        }),
        `${action} should be valid`,
      );
    }
    assert.equal(
      Value.Check(ConfirmPositionParams, {
        position_id: "p1",
        action: "invalid",
        idempotency_key: "k1",
      }),
      false,
      "Invalid action should fail",
    );
  });

  it("ConfirmPositionParams validates adjust with amount_eur", () => {
    assert.ok(
      Value.Check(ConfirmPositionParams, {
        position_id: "p1",
        action: "adjust",
        amount_eur: 1500,
        idempotency_key: "k1",
      }),
      "adjust with amount is valid",
    );
    assert.ok(
      Value.Check(ConfirmPositionParams, {
        position_id: "p1",
        action: "adjust",
        idempotency_key: "k1",
      }),
      "adjust without amount is valid (optional)",
    );
    assert.equal(
      Value.Check(ConfirmPositionParams, {
        position_id: "p1",
        action: "adjust",
        amount_eur: -100,
        idempotency_key: "k1",
      }),
      false,
      "Negative amount_eur should fail",
    );
  });
});
