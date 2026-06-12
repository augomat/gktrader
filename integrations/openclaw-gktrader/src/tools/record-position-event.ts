import type { ToolPluginToolDefinition } from "../plugin-sdk.js";
import { RecordPositionEventParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";

export const recordPositionEventTool: ToolPluginToolDefinition<
  { apiBaseUrl: string; sharedSecret: string },
  typeof RecordPositionEventParams
> = {
  name: "gktrader_record_position_event",
  label: "Record Position Event",
  description:
    "Record a manual position event (open, increase, reduce, close, confirm, adjust) for a ticker. " +
    "Requires an idempotency key. Returns the persisted position event ID.",
  parameters: RecordPositionEventParams,

  async execute(params, config, _context) {
    const client = new GkTraderClient({
      baseUrl: config.apiBaseUrl,
      sharedSecret: config.sharedSecret,
    });
    const result = await client.recordPositionEvent(
      {
        ticker: params.ticker,
        event_type: params.event_type,
        amount_eur: params.amount_eur,
        price: params.price ?? null,
        notes: params.notes ?? null,
      },
      params.idempotency_key,
    );
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      details: { position_event: result },
    };
  },
};
