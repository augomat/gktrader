import type { ToolPluginToolDefinition } from "../plugin-sdk.js";
import { GetAlertParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";

export const getAlertTool: ToolPluginToolDefinition<
  { apiBaseUrl: string; sharedSecret: string },
  typeof GetAlertParams
> = {
  name: "gktrader_get_alert",
  label: "Get GKTrader Alert",
  description:
    "Fetch a single GKTrader alert by ID. Returns alert level, rendered payload, and metadata.",
  parameters: GetAlertParams,

  async execute(params, config, _context) {
    const client = new GkTraderClient({
      baseUrl: config.apiBaseUrl,
      sharedSecret: config.sharedSecret,
    });
    const alert = await client.getAlert(params.alert_id);
    return {
      content: [{ type: "text", text: JSON.stringify(alert, null, 2) }],
      details: { alert },
    };
  },
};
