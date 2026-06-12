import type { ToolPluginToolDefinition } from "../plugin-sdk.js";
import { ListPositionsParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";

export const listPositionsTool: ToolPluginToolDefinition<
  { apiBaseUrl: string; sharedSecret: string },
  typeof ListPositionsParams
> = {
  name: "gktrader_list_positions",
  label: "List GKTrader Positions",
  description:
    "List all current projected positions with ticker, direction, net amount (EUR), average price, and last-updated timestamp.",
  parameters: ListPositionsParams,

  async execute(_params, config, _context) {
    const client = new GkTraderClient({
      baseUrl: config.apiBaseUrl,
      sharedSecret: config.sharedSecret,
    });
    const positions = await client.listPositions();
    return {
      content: [{ type: "text", text: JSON.stringify(positions, null, 2) }],
      details: { count: positions.length, positions },
    };
  },
};
