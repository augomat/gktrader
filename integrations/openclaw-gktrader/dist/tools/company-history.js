import { CompanyHistoryParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";
export const companyHistoryTool = {
    name: "gktrader_company_history",
    label: "Company Signal History",
    description: "Fetch all prior bullish signals for a company/ticker. Returns ticker, signal list with source date, event type, alert level, and rationale. Used for bearish-alert pre-history and company context queries.",
    parameters: CompanyHistoryParams,
    async execute(params, config, _context) {
        const client = new GkTraderClient({
            baseUrl: config.apiBaseUrl,
            sharedSecret: config.sharedSecret,
        });
        const history = await client.companyHistory(params.ticker);
        return {
            content: [{ type: "text", text: JSON.stringify(history, null, 2) }],
            details: { history },
        };
    },
};
//# sourceMappingURL=company-history.js.map