/**
 * GKTrader tool parameter schemas.
 *
 * Every tool parameter set is a TypeBox schema that matches the
 * corresponding internal FastAPI endpoint contract.
 *
 * Side-effecting tools (record_decision, snooze_alert,
 * record_position_event) require an idempotency_key parameter.
 * The API client sends it as the `Idempotency-Key` header.
 */
import { Type, type Static } from "typebox";
export declare const GetAlertParams: Type.TObject<{
    alert_id: Type.TString;
}>;
export type GetAlertParams = Static<typeof GetAlertParams>;
export declare const RecentAlertsParams: Type.TObject<{
    limit: Type.TOptional<Type.TInteger>;
}>;
export type RecentAlertsParams = Static<typeof RecentAlertsParams>;
export declare const RecordDecisionParams: Type.TObject<{
    alert_id: Type.TString;
    decision: Type.TUnion<[Type.TLiteral<"bought">, Type.TLiteral<"sold_reduced">, Type.TLiteral<"shorted">, Type.TLiteral<"no_trade">]>;
    amount_eur: Type.TOptional<Type.TNumber>;
    execution_price: Type.TOptional<Type.TNumber>;
    notes: Type.TOptional<Type.TString>;
    idempotency_key: Type.TString;
}>;
export type RecordDecisionParams = Static<typeof RecordDecisionParams>;
export declare const SnoozeAlertParams: Type.TObject<{
    alert_id: Type.TString;
    minutes: Type.TInteger;
    idempotency_key: Type.TString;
}>;
export type SnoozeAlertParams = Static<typeof SnoozeAlertParams>;
export declare const ListPositionsParams: Type.TObject<{}>;
export type ListPositionsParams = Static<typeof ListPositionsParams>;
export declare const RecordPositionEventParams: Type.TObject<{
    ticker: Type.TString;
    event_type: Type.TUnion<[Type.TLiteral<"open">, Type.TLiteral<"increase">, Type.TLiteral<"reduce">, Type.TLiteral<"close">, Type.TLiteral<"confirm">, Type.TLiteral<"adjust">]>;
    amount_eur: Type.TNumber;
    price: Type.TOptional<Type.TNumber>;
    notes: Type.TOptional<Type.TString>;
    idempotency_key: Type.TString;
}>;
export type RecordPositionEventParams = Static<typeof RecordPositionEventParams>;
export declare const CompanyHistoryParams: Type.TObject<{
    ticker: Type.TString;
}>;
export type CompanyHistoryParams = Static<typeof CompanyHistoryParams>;
export declare const WeeklyReviewParams: Type.TObject<{}>;
export type WeeklyReviewParams = Static<typeof WeeklyReviewParams>;
export declare const ConfirmPositionParams: Type.TObject<{
    position_id: Type.TString;
    action: Type.TUnion<[Type.TLiteral<"keep_open">, Type.TLiteral<"close">, Type.TLiteral<"adjust">]>;
    amount_eur: Type.TOptional<Type.TNumber>;
    idempotency_key: Type.TString;
}>;
export type ConfirmPositionParams = Static<typeof ConfirmPositionParams>;
//# sourceMappingURL=schemas.d.ts.map