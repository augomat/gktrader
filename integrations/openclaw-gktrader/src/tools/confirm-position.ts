import type { ToolPluginToolDefinition } from "../plugin-sdk.js";
import { ConfirmPositionParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";

export const confirmPositionTool: ToolPluginToolDefinition<
  { apiBaseUrl: string; sharedSecret: string },
  typeof ConfirmPositionParams
> = {
  name: "gktrader_confirm_position",
  label: "Confirm Position",
  description:
    "Confirm, close, or adjust a position from the weekly review. " +
    "Use keep_open to confirm the current position, close to mark it closed, " +
    "or adjust to update the amount. Requires an idempotency key.",
  parameters: ConfirmPositionParams,

  async execute(params, config, _context) {
    const client = new GkTraderClient({
      baseUrl: config.apiBaseUrl,
      sharedSecret: config.sharedSecret,
    });
    const result = await client.confirmPosition(
      params.position_id,
      {
        action: params.action,
        amount_eur: params.amount_eur ?? null,
      },
      params.idempotency_key,
    );
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      details: { confirmation: result },
    };
  },
};
