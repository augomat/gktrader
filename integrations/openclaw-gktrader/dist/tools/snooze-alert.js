import { SnoozeAlertParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";
export const snoozeAlertTool = {
    name: "gktrader_snooze_alert",
    label: "Snooze Alert",
    description: "Snooze a GKTrader alert for a configurable number of minutes (1-1440, default 30). Requires an idempotency key.",
    parameters: SnoozeAlertParams,
    async execute(params, config, _context) {
        const client = new GkTraderClient({
            baseUrl: config.apiBaseUrl,
            sharedSecret: config.sharedSecret,
        });
        const result = await client.snoozeAlert(params.alert_id, params.minutes, params.idempotency_key);
        return {
            content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
            details: { snooze: result },
        };
    },
};
//# sourceMappingURL=snooze-alert.js.map