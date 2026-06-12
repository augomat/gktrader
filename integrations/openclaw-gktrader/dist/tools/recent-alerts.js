import { RecentAlertsParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";
export const recentAlertsTool = {
    name: "gktrader_recent_alerts",
    label: "Recent GKTrader Alerts",
    description: "Fetch the most recent GKTrader alerts (up to 50). Returns alert ID, level, and rendered payload for each.",
    parameters: RecentAlertsParams,
    async execute(params, config, _context) {
        const client = new GkTraderClient({
            baseUrl: config.apiBaseUrl,
            sharedSecret: config.sharedSecret,
        });
        const alerts = await client.recentAlerts(params.limit ?? 20);
        return {
            content: [{ type: "text", text: JSON.stringify(alerts, null, 2) }],
            details: { count: alerts.length, alerts },
        };
    },
};
//# sourceMappingURL=recent-alerts.js.map