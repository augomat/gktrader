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
export declare class GkTraderClient {
    #private;
    constructor(opts: {
        baseUrl: string;
        sharedSecret: string;
    });
    healthz(): Promise<{
        status: string;
    }>;
    readyz(): Promise<{
        status: string;
    }>;
    getAlert(id: string): Promise<Record<string, unknown>>;
    recentAlerts(limit?: number): Promise<Record<string, unknown>[]>;
    recordAlertDecision(alertId: string, body: {
        decision: string;
        amount_eur?: number | null;
        execution_price?: number | null;
        notes?: string | null;
    }, idempotencyKey: string): Promise<Record<string, unknown>>;
    snoozeAlert(alertId: string, minutes: number, idempotencyKey: string): Promise<Record<string, unknown>>;
    getEvent(id: string): Promise<Record<string, unknown>>;
    companyHistory(ticker: string): Promise<Record<string, unknown>>;
    listPositions(): Promise<Record<string, unknown>[]>;
    recordPositionEvent(body: {
        ticker: string;
        event_type: string;
        amount_eur: number;
        price?: number | null;
        notes?: string | null;
    }, idempotencyKey: string): Promise<Record<string, unknown>>;
    weeklyReview(): Promise<Record<string, unknown>>;
    confirmPosition(positionId: string, body: {
        action: string;
        amount_eur?: number | null;
    }, idempotencyKey: string): Promise<Record<string, unknown>>;
}
export declare class GkTraderApiError extends Error {
    readonly status: number;
    constructor(status: number, detail: string);
}
//# sourceMappingURL=client.d.ts.map