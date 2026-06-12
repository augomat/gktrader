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
  readonly #baseUrl: string;
  readonly #secret: string;

  constructor(opts: { baseUrl: string; sharedSecret: string }) {
    this.#baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.#secret = opts.sharedSecret;
  }

  /** Shared headers sent on every request. */
  #headers(extra: Record<string, string> = {}): Record<string, string> {
    return {
      "X-GKTrader-Secret": this.#secret,
      Accept: "application/json",
      ...extra,
    };
  }

  // ---- health -----------------------------------------------------------

  async healthz(): Promise<{ status: string }> {
    return this.#get("/healthz");
  }

  async readyz(): Promise<{ status: string }> {
    return this.#get("/readyz");
  }

  // ---- alerts -----------------------------------------------------------

  async getAlert(id: string): Promise<Record<string, unknown>> {
    return this.#get(`/v1/alerts/${encodeURIComponent(id)}`);
  }

  async recentAlerts(limit?: number): Promise<Record<string, unknown>[]> {
    const params = new URLSearchParams();
    if (limit !== undefined) params.set("limit", String(limit));
    const qs = params.toString();
    return this.#get(`/v1/alerts/recent${qs ? "?" + qs : ""}`);
  }

  async recordAlertDecision(
    alertId: string,
    body: {
      decision: string;
      amount_eur?: number | null;
      execution_price?: number | null;
      notes?: string | null;
    },
    idempotencyKey: string,
  ): Promise<Record<string, unknown>> {
    return this.#post(
      `/v1/alerts/${encodeURIComponent(alertId)}/decision`,
      body,
      idempotencyKey,
    );
  }

  async snoozeAlert(
    alertId: string,
    minutes: number,
    idempotencyKey: string,
  ): Promise<Record<string, unknown>> {
    return this.#post(
      `/v1/alerts/${encodeURIComponent(alertId)}/snooze`,
      { minutes },
      idempotencyKey,
    );
  }

  // ---- events -----------------------------------------------------------

  async getEvent(id: string): Promise<Record<string, unknown>> {
    return this.#get(`/v1/events/${encodeURIComponent(id)}`);
  }

  // ---- companies --------------------------------------------------------

  async companyHistory(ticker: string): Promise<Record<string, unknown>> {
    return this.#get(`/v1/companies/${encodeURIComponent(ticker)}/history`);
  }

  // ---- positions --------------------------------------------------------

  async listPositions(): Promise<Record<string, unknown>[]> {
    return this.#get("/v1/positions");
  }

  async recordPositionEvent(
    body: {
      ticker: string;
      event_type: string;
      amount_eur: number;
      price?: number | null;
      notes?: string | null;
    },
    idempotencyKey: string,
  ): Promise<Record<string, unknown>> {
    return this.#post("/v1/positions/events", body, idempotencyKey);
  }

  // ---- weekly review ----------------------------------------------------

  async weeklyReview(): Promise<Record<string, unknown>> {
    return this.#get("/v1/reviews/weekly");
  }

  async confirmPosition(
    positionId: string,
    body: { action: string; amount_eur?: number | null },
    idempotencyKey: string,
  ): Promise<Record<string, unknown>> {
    return this.#post(
      `/v1/reviews/positions/${encodeURIComponent(positionId)}/confirm`,
      body,
      idempotencyKey,
    );
  }

  // ---- HTTP helpers -----------------------------------------------------

  async #get(path: string): Promise<any> {
    const res = await fetch(`${this.#baseUrl}${path}`, {
      method: "GET",
      headers: this.#headers(),
    });
    await this.#check(res);
    return res.json();
  }

  async #post(
    path: string,
    body: unknown,
    idempotencyKey?: string,
  ): Promise<any> {
    const extra: Record<string, string> = {};
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

  async #check(res: Response): Promise<void> {
    if (res.ok) return;
    let detail = "";
    try {
      const body = await res.json();
      detail = (body as any)?.detail ?? JSON.stringify(body);
    } catch {
      detail = await res.text().catch(() => "");
    }
    throw new GkTraderApiError(res.status, detail || res.statusText);
  }
}

export class GkTraderApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(`GKTrader API ${status}: ${detail}`);
    this.name = "GkTraderApiError";
    this.status = status;
  }
}
