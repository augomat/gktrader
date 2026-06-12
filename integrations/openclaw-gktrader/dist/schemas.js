/**
 * GKTrader tool parameter schemas.
 *
 * Every tool parameter set is a TypeBox schema that matches the
 * corresponding internal FastAPI endpoint contract.
 *
 * Side-effecting tools (record_decision, snooze_alert,
 * record_position_event) require an idempotency_key parameter.
 * The API client sends it as the `Idempotency-Key` header.
 */
import { Type } from "typebox";
// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------
/** Positive non-zero amount in EUR. */
const AmountEur = Type.Optional(Type.Number({
    description: "EUR amount (positive, > 0).",
    exclusiveMinimum: 0,
}));
/** Positive non-zero execution / entry price. */
const ExecutionPrice = Type.Optional(Type.Number({
    description: "Execution or estimated price (positive, > 0).",
    exclusiveMinimum: 0,
}));
/** Free-form notes (optional). */
const Notes = Type.Optional(Type.String({ description: "Optional free-form notes." }));
/** Idempotency key required for all side-effecting requests. */
const IdempotencyKey = Type.String({
    description: "Unique idempotency key (UUID recommended). Duplicate keys safely replay the same side effect.",
    minLength: 1,
    maxLength: 128,
});
// ---------------------------------------------------------------------------
// gktrader_get_alert
// ---------------------------------------------------------------------------
export const GetAlertParams = Type.Object({
    alert_id: Type.String({
        description: "Alert ID returned by a previous alert delivery or listing.",
        minLength: 1,
    }),
}, { description: "Fetch a single alert by ID." });
// ---------------------------------------------------------------------------
// gktrader_recent_alerts
// ---------------------------------------------------------------------------
export const RecentAlertsParams = Type.Object({
    limit: Type.Optional(Type.Integer({
        description: "Maximum number of recent alerts to return (default 20, max 50).",
        minimum: 1,
        maximum: 50,
        default: 20,
    })),
}, { description: "Fetch the most recent alerts." });
// ---------------------------------------------------------------------------
// gktrader_record_decision
// ---------------------------------------------------------------------------
export const RecordDecisionParams = Type.Object({
    alert_id: Type.String({
        description: "Alert ID to record a decision against.",
        minLength: 1,
    }),
    decision: Type.Union([
        Type.Literal("bought"),
        Type.Literal("sold_reduced"),
        Type.Literal("shorted"),
        Type.Literal("no_trade"),
    ], {
        description: "Trade decision: bought, sold_reduced, shorted, or no_trade.",
    }),
    amount_eur: AmountEur,
    execution_price: ExecutionPrice,
    notes: Notes,
    idempotency_key: IdempotencyKey,
}, { description: "Record a trade decision for an alert." });
// ---------------------------------------------------------------------------
// gktrader_snooze_alert
// ---------------------------------------------------------------------------
export const SnoozeAlertParams = Type.Object({
    alert_id: Type.String({
        description: "Alert ID to snooze.",
        minLength: 1,
    }),
    minutes: Type.Integer({
        description: "Number of minutes to snooze (1-1440, default 30).",
        minimum: 1,
        maximum: 1440,
        default: 30,
    }),
    idempotency_key: IdempotencyKey,
}, { description: "Snooze an alert for a number of minutes." });
// ---------------------------------------------------------------------------
// gktrader_list_positions
// ---------------------------------------------------------------------------
export const ListPositionsParams = Type.Object({}, { description: "List all current projected positions." });
// ---------------------------------------------------------------------------
// gktrader_record_position_event
// ---------------------------------------------------------------------------
export const RecordPositionEventParams = Type.Object({
    ticker: Type.String({
        description: "Uppercase ticker symbol (e.g. AAPL, RGTI).",
        minLength: 1,
        maxLength: 10,
        pattern: "^[A-Z0-9.]+$",
    }),
    event_type: Type.Union([
        Type.Literal("open"),
        Type.Literal("increase"),
        Type.Literal("reduce"),
        Type.Literal("close"),
        Type.Literal("confirm"),
        Type.Literal("adjust"),
    ], {
        description: "Position event type: open, increase, reduce, close, confirm, adjust.",
    }),
    amount_eur: Type.Number({
        description: "EUR amount for the event (non-negative).",
        minimum: 0,
    }),
    price: ExecutionPrice,
    notes: Notes,
    idempotency_key: IdempotencyKey,
}, { description: "Record a manual position event." });
// ---------------------------------------------------------------------------
// gktrader_company_history
// ---------------------------------------------------------------------------
export const CompanyHistoryParams = Type.Object({
    ticker: Type.String({
        description: "Uppercase ticker symbol (e.g. AAPL, RGTI).",
        minLength: 1,
        maxLength: 10,
    }),
}, { description: "Fetch all prior bullish signals for a company/ticker." });
// ---------------------------------------------------------------------------
// gktrader_weekly_review
// ---------------------------------------------------------------------------
export const WeeklyReviewParams = Type.Object({}, { description: "Fetch the latest weekly review with open positions." });
// ---------------------------------------------------------------------------
// gktrader_confirm_position
// ---------------------------------------------------------------------------
export const ConfirmPositionParams = Type.Object({
    position_id: Type.String({
        description: "Position ID from the weekly review open positions list.",
        minLength: 1,
    }),
    action: Type.Union([
        Type.Literal("keep_open"),
        Type.Literal("close"),
        Type.Literal("adjust"),
    ], {
        description: "Confirmation action: keep_open, close, or adjust.",
    }),
    amount_eur: Type.Optional(Type.Number({
        description: "New EUR amount (required for adjust, ignored otherwise).",
        minimum: 0,
    })),
    idempotency_key: IdempotencyKey,
}, { description: "Confirm, close, or adjust a position from the weekly review." });
//# sourceMappingURL=schemas.js.map