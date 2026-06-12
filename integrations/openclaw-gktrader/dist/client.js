/**
 * Shared-secret GKTrader loopback API client.
 *
 * Every request includes the `X-GKTrader-Secret` authorization header.
 * Idempotency keys are sent as `Idempotency-Key` headers on POST/PUT.
 *
 * The client is intentionally minimal: no shell, browser, broker, or
 * order-execution capability. Only the endpoints defined in the internal
 * API contract are callable.
 */
export class GkTraderClient {
    #baseUrl;
    #secret;
    constructor(opts) {
        this.#baseUrl = opts.baseUrl.replace(/\/+$/, "");
        this.#secret = opts.sharedSecret;
    }
    /** Shared headers sent on every request. */
    #headers(extra = {}) {
        return {
            "X-GKTrader-Secret": this.#secret,
            Accept: "application/json",
            ...extra,
        };
    }
    // ---- health -----------------------------------------------------------
    async healthz() {
        return this.#get("/healthz");
    }
    async readyz() {
        return this.#get("/readyz");
    }
    // ---- alerts -----------------------------------------------------------
    async getAlert(id) {
        return this.#get(`/v1/alerts/${encodeURIComponent(id)}`);
    }
    async recentAlerts(limit) {
        const params = new URLSearchParams();
        if (limit !== undefined)
            params.set("limit", String(limit));
        const qs = params.toString();
        return this.#get(`/v1/alerts/recent${qs ? "?" + qs : ""}`);
    }
    async recordAlertDecision(alertId, body, idempotencyKey) {
        return this.#post(`/v1/alerts/${encodeURIComponent(alertId)}/decision`, body, idempotencyKey);
    }
    async snoozeAlert(alertId, minutes, idempotencyKey) {
        return this.#post(`/v1/alerts/${encodeURIComponent(alertId)}/snooze`, { minutes }, idempotencyKey);
    }
    // ---- events -----------------------------------------------------------
    async getEvent(id) {
        return this.#get(`/v1/events/${encodeURIComponent(id)}`);
    }
    // ---- companies --------------------------------------------------------
    async companyHistory(ticker) {
        return this.#get(`/v1/companies/${encodeURIComponent(ticker)}/history`);
    }
    // ---- positions --------------------------------------------------------
    async listPositions() {
        return this.#get("/v1/positions");
    }
    async recordPositionEvent(body, idempotencyKey) {
        return this.#post("/v1/positions/events", body, idempotencyKey);
    }
    // ---- weekly review ----------------------------------------------------
    async weeklyReview() {
        return this.#get("/v1/reviews/weekly");
    }
    async confirmPosition(positionId, body, idempotencyKey) {
        return this.#post(`/v1/reviews/positions/${encodeURIComponent(positionId)}/confirm`, body, idempotencyKey);
    }
    // ---- HTTP helpers -----------------------------------------------------
    async #get(path) {
        const res = await fetch(`${this.#baseUrl}${path}`, {
            method: "GET",
            headers: this.#headers(),
        });
        await this.#check(res);
        return res.json();
    }
    async #post(path, body, idempotencyKey) {
        const extra = {};
        if (idempotencyKey) {
            extra["Idempotency-Key"] = idempotencyKey;
        }
        if (body && typeof body === "object") {
            extra["Content-Type"] = "application/json";
        }
        const res = await fetch(`${this.#baseUrl}${path}`, {
            method: "POST",
            headers: this.#headers(extra),
            body: body ? JSON.stringify(body) : undefined,
        });
        await this.#check(res);
        return res.json();
    }
    async #check(res) {
        if (res.ok)
            return;
        let detail = "";
        try {
            const body = await res.json();
            detail = body?.detail ?? JSON.stringify(body);
        }
        catch {
            detail = await res.text().catch(() => "");
        }
        throw new GkTraderApiError(res.status, detail || res.statusText);
    }
}
export class GkTraderApiError extends Error {
    status;
    constructor(status, detail) {
        super(`GKTrader API ${status}: ${detail}`);
        this.name = "GkTraderApiError";
        this.status = status;
    }
}
//# sourceMappingURL=client.js.map