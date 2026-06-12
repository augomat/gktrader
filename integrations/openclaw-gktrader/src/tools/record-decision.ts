import type { ToolPluginToolDefinition } from "../plugin-sdk.js";
import { RecordDecisionParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";

export const recordDecisionTool: ToolPluginToolDefinition<
  { apiBaseUrl: string; sharedSecret: string },
  typeof RecordDecisionParams
> = {
  name: "gktrader_record_decision",
  label: "Record Trade Decision",
  description:
    "Record a trade decision (bought, sold_reduced, shorted, or no_trade) against a GKTrader alert. " +
    "Requires an idempotency key. If the decision is bought/shorted/sold_reduced, a position event " +
    "is automatically created. Returns the decision ID and optional position event ID.",
  parameters: RecordDecisionParams,

  async execute(params, config, _context) {
    const client = new GkTraderClient({
      baseUrl: config.apiBaseUrl,
      sharedSecret: config.sharedSecret,
    });
    const result = await client.recordAlertDecision(
      params.alert_id,
      {
        decision: params.decision,
        amount_eur: params.amount_eur ?? null,
        execution_price: params.execution_price ?? null,
        notes: params.notes ?? null,
      },
      params.idempotency_key,
    );
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      details: { decision: result },
    };
  },
};
