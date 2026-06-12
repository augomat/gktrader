import { WeeklyReviewParams } from "../schemas.js";
import { GkTraderClient } from "../client.js";
export const weeklyReviewTool = {
    name: "gktrader_weekly_review",
    label: "Weekly Review",
    description: "Fetch the latest weekly review with summary and all open positions that need confirmation. Returns generated timestamp, summary text, and per-position status.",
    parameters: WeeklyReviewParams,
    async execute(_params, config, _context) {
        const client = new GkTraderClient({
            baseUrl: config.apiBaseUrl,
            sharedSecret: config.sharedSecret,
        });
        const review = await client.weeklyReview();
        return {
            content: [{ type: "text", text: JSON.stringify(review, null, 2) }],
            details: { review },
        };
    },
};
//# sourceMappingURL=weekly-review.js.map