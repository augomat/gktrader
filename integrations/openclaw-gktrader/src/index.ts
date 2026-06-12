/**
 * GKTrader restricted OpenClaw plugin.
 *
 * Provides exactly 9 tools that call only the loopback FastAPI internal
 * service. No shell, browser, broker, or order-execution tools.
 *
 * Tools:
 *   gktrader_get_alert             – GET  /v1/alerts/{id}
 *   gktrader_recent_alerts         – GET  /v1/alerts/recent
 *   gktrader_record_decision       – POST /v1/alerts/{id}/decision
 *   gktrader_snooze_alert          – POST /v1/alerts/{id}/snooze
 *   gktrader_list_positions        – GET  /v1/positions
 *   gktrader_record_position_event – POST /v1/positions/events
 *   gktrader_company_history       – GET  /v1/companies/{ticker}/history
 *   gktrader_weekly_review         – GET  /v1/reviews/weekly
 *   gktrader_confirm_position      – POST /v1/reviews/positions/{id}/confirm
 */
import { Type } from "typebox";
import { defineToolPlugin, type ToolDefinition } from "./plugin-sdk.js";
import { getAlertTool } from "./tools/get-alert.js";
import { recentAlertsTool } from "./tools/recent-alerts.js";
import { recordDecisionTool } from "./tools/record-decision.js";
import { snoozeAlertTool } from "./tools/snooze-alert.js";
import { listPositionsTool } from "./tools/list-positions.js";
import { recordPositionEventTool } from "./tools/record-position-event.js";
import { companyHistoryTool } from "./tools/company-history.js";
import { weeklyReviewTool } from "./tools/weekly-review.js";
import { confirmPositionTool } from "./tools/confirm-position.js";

export type { ToolPluginToolDefinition } from "./plugin-sdk.js";
export { GkTraderClient, GkTraderApiError } from "./client.js";

const PluginConfigSchema = Type.Object(
  {
    apiBaseUrl: Type.String({
      description: "GKTrader internal loopback API base URL.",
      default: "http://127.0.0.1:8000",
    }),
    sharedSecret: Type.String({
      description: "Shared secret matching GKTRADER_INTERNAL_API_SHARED_SECRET.",
      minLength: 1,
    }),
  },
  { additionalProperties: false },
);

/** Widen a typed tool definition to the opaque ToolDefinition shape. */
function asToolDef(def: {
  name: string;
  label?: string;
  description: string;
  parameters: unknown;
  optional?: boolean;
  execute: Function;
}): ToolDefinition {
  return {
    name: def.name,
    label: def.label ?? def.name,
    description: def.description,
    parameters: def.parameters,
    optional: def.optional ?? false,
    execute: def.execute as ToolDefinition["execute"],
  };
}

export default defineToolPlugin({
  id: "openclaw-gktrader",
  name: "GKTrader",
  description:
    "Restricted GKTrader event-signal tools. Call only the loopback FastAPI. " +
    "No shell, browser, broker, or order-execution capability.",

  configSchema: PluginConfigSchema,

  tools: [
    asToolDef(getAlertTool),
    asToolDef(recentAlertsTool),
    asToolDef(recordDecisionTool),
    asToolDef(snoozeAlertTool),
    asToolDef(listPositionsTool),
    asToolDef(recordPositionEventTool),
    asToolDef(companyHistoryTool),
    asToolDef(weeklyReviewTool),
    asToolDef(confirmPositionTool),
  ],
});
