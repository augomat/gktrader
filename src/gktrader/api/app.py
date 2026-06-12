from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from gktrader.api.services import ApiService
from gktrader.config.settings import get_settings
from gktrader.db.session import get_db
from gktrader.domain.contracts import (
    AlertDecisionRequest,
    AlertDecisionResponse,
    CompanyHistoryResponse,
    PositionConfirmationRequest,
    PositionEventRequest,
    PositionSummary,
    SnoozeAlertRequest,
    WeeklyReviewResponse,
)


def authorize(x_gktrader_secret: str = Header(default="")) -> None:
    settings = get_settings()
    if x_gktrader_secret != settings.internal_api_shared_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def get_service(db: Session = Depends(get_db)) -> ApiService:
    return ApiService(db)


def create_app() -> FastAPI:
    app = FastAPI(title="GKTrader Internal API", version="0.1.0")

    from gktrader.ui.routes import router as ui_router
    app.include_router(ui_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", dependencies=[Depends(authorize)])
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/v1/alerts/recent", dependencies=[Depends(authorize)])
    def recent_alerts(service: ApiService = Depends(get_service)) -> list[dict]:
        return service.recent_alerts()

    @app.get("/v1/alerts/{alert_id}", dependencies=[Depends(authorize)])
    def get_alert(alert_id: str, service: ApiService = Depends(get_service)) -> dict:
        return service.get_alert(alert_id)

    @app.post("/v1/alerts/{alert_id}/decision", dependencies=[Depends(authorize)])
    def record_alert_decision(
        alert_id: str,
        payload: AlertDecisionRequest,
        response: Response,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        service: ApiService = Depends(get_service),
    ) -> AlertDecisionResponse:
        response.status_code = status.HTTP_201_CREATED
        return service.record_alert_decision(alert_id, payload, idempotency_key)

    @app.post("/v1/alerts/{alert_id}/snooze", dependencies=[Depends(authorize)])
    def snooze_alert(
        alert_id: str,
        payload: SnoozeAlertRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        service: ApiService = Depends(get_service),
    ) -> dict:
        return service.snooze_alert(alert_id, payload.minutes, idempotency_key)

    @app.get("/v1/events/{event_id}", dependencies=[Depends(authorize)])
    def get_event(event_id: str, service: ApiService = Depends(get_service)) -> dict:
        return service.get_event(event_id)

    @app.get("/v1/companies/{ticker}/history", dependencies=[Depends(authorize)])
    def company_history(ticker: str, service: ApiService = Depends(get_service)) -> CompanyHistoryResponse:
        return service.company_history(ticker)

    @app.get("/v1/positions", dependencies=[Depends(authorize)])
    def list_positions(service: ApiService = Depends(get_service)) -> list[PositionSummary]:
        return service.list_positions()

    @app.post("/v1/positions/events", dependencies=[Depends(authorize)])
    def record_position_event(
        payload: PositionEventRequest,
        response: Response,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        service: ApiService = Depends(get_service),
    ) -> dict:
        response.status_code = status.HTTP_201_CREATED
        return service.record_position_event(payload, idempotency_key)

    @app.get("/v1/reviews/weekly", dependencies=[Depends(authorize)])
    def weekly_review(service: ApiService = Depends(get_service)) -> WeeklyReviewResponse:
        return service.get_weekly_review(datetime.now(UTC))

    @app.post("/v1/reviews/positions/{position_id}/confirm", dependencies=[Depends(authorize)])
    def confirm_position(
        position_id: str,
        payload: PositionConfirmationRequest,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        service: ApiService = Depends(get_service),
    ) -> dict:
        return service.confirm_position(position_id, payload, idempotency_key)

    return app
