from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from gktrader.db.session import get_db
from gktrader.domain.contracts import AlertDecisionRequest, PositionEventRequest, SnoozeAlertRequest
from gktrader.domain.enums import PositionEventType, TradeDecisionType
from gktrader.ui.auth import get_session, make_session_token
from gktrader.ui.service import UIService

router = APIRouter(prefix="/ui")
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

_LEVEL_CSS = {
    "TRADEABLE": "tradeable",
    "REVIEW": "review",
    "WATCH": "watch",
    "AVOID_CHASE": "avoid",
    "IGNORE": "ignore",
}
_LEVEL_DEFAULT_NOTIONAL = {
    "TRADEABLE": 1000.0,
    "REVIEW": 500.0,
    "WATCH": 0.0,
    "AVOID_CHASE": 0.0,
    "IGNORE": 0.0,
}

templates.env.globals["level_css"] = _LEVEL_CSS
templates.env.globals["level_notional"] = _LEVEL_DEFAULT_NOTIONAL


def _svc(db: Session = Depends(get_db)) -> UIService:
    return UIService(db)


def _guard(request: Request) -> bool:
    return get_session(request)


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def login_submit(request: Request, secret: str = Form(...)):
    from gktrader.config.settings import get_settings
    settings = get_settings()
    if secret != settings.internal_api_shared_secret:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid secret"}, status_code=401
        )
    token = make_session_token(settings.internal_api_shared_secret)
    response = RedirectResponse("/ui/dashboard", status_code=303)
    response.set_cookie("gkt_session", token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/ui/login", status_code=303)
    response.delete_cookie("gkt_session")
    return response


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def index():
    return RedirectResponse("/ui/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return RedirectResponse("/ui/login", status_code=303)
    stats = svc.dashboard_stats()
    alerts = svc.recent_alerts(limit=8)
    positions = svc.list_positions()
    health = svc.pipeline_health()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "page": "dashboard",
        "stats": stats,
        "alerts": alerts,
        "positions": positions,
        "health": health,
        "level_css": _LEVEL_CSS,
    })


# ------------------------------------------------------------------
# Alerts
# ------------------------------------------------------------------

@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return RedirectResponse("/ui/login", status_code=303)
    alerts = svc.recent_alerts(limit=50)
    return templates.TemplateResponse("alerts.html", {
        "request": request,
        "page": "alerts",
        "alerts": alerts,
        "level_css": _LEVEL_CSS,
    })


@router.get("/partials/alert-feed", response_class=HTMLResponse)
async def alert_feed_partial(request: Request, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return HTMLResponse("", status_code=401)
    alerts = svc.recent_alerts(limit=50)
    return templates.TemplateResponse("partials/alert_feed.html", {
        "request": request,
        "alerts": alerts,
        "level_css": _LEVEL_CSS,
    })


@router.get("/alerts/{alert_id}", response_class=HTMLResponse)
async def alert_detail(request: Request, alert_id: str, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return RedirectResponse("/ui/login", status_code=303)
    alert = svc.get_alert_detail(alert_id)
    if not alert:
        return HTMLResponse("Alert not found", status_code=404)
    return templates.TemplateResponse("alert_detail.html", {
        "request": request,
        "page": "alerts",
        "alert": alert,
        "level_css": _LEVEL_CSS,
        "level_notional": _LEVEL_DEFAULT_NOTIONAL,
    })


@router.post("/alerts/{alert_id}/decision", response_class=HTMLResponse)
async def record_decision(
    request: Request,
    alert_id: str,
    decision: str = Form(...),
    amount_eur: float | None = Form(default=None),
    execution_price: float | None = Form(default=None),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    if not _guard(request):
        return HTMLResponse("", status_code=401)
    from gktrader.api.services import ApiService
    svc = ApiService(db)
    req = AlertDecisionRequest(
        decision=TradeDecisionType(decision),
        amount_eur=amount_eur or None,
        execution_price=execution_price or None,
        notes=notes or None,
    )
    svc.record_alert_decision(alert_id, req, str(uuid.uuid4()))
    # Return a minimal confirmation fragment for HTMX swap
    return HTMLResponse(
        f'<div class="decision-recorded">✓ {decision.upper()} recorded'
        + (f" · EUR {amount_eur:,.0f}" if amount_eur else "")
        + "</div>"
    )


@router.post("/alerts/{alert_id}/snooze", response_class=HTMLResponse)
async def snooze_alert(
    request: Request,
    alert_id: str,
    minutes: int = Form(default=30),
    db: Session = Depends(get_db),
):
    if not _guard(request):
        return HTMLResponse("", status_code=401)
    from gktrader.api.services import ApiService
    svc = ApiService(db)
    svc.snooze_alert(alert_id, minutes, str(uuid.uuid4()))
    return HTMLResponse(f'<span class="snooze-done">⏰ Snoozed {minutes}m</span>')


# ------------------------------------------------------------------
# Positions
# ------------------------------------------------------------------

@router.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return RedirectResponse("/ui/login", status_code=303)
    positions = svc.list_positions()
    events = svc.position_events_log()
    summary = svc.position_summary()
    return templates.TemplateResponse("positions.html", {
        "request": request,
        "page": "positions",
        "positions": positions,
        "events": events,
        "summary": summary,
    })


@router.post("/positions/event", response_class=HTMLResponse)
async def record_position_event(
    request: Request,
    ticker: str = Form(...),
    event_type: str = Form(...),
    amount_eur: float | None = Form(default=None),
    price: float | None = Form(default=None),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    if not _guard(request):
        return HTMLResponse("", status_code=401)
    from gktrader.api.services import ApiService
    svc = ApiService(db)
    req = PositionEventRequest(
        ticker=ticker.upper(),
        event_type=PositionEventType(event_type),
        amount_eur=amount_eur,
        price=price,
        notes=notes or None,
    )
    svc.record_position_event(req, str(uuid.uuid4()))
    return RedirectResponse("/ui/positions", status_code=303)


# ------------------------------------------------------------------
# Performance
# ------------------------------------------------------------------

@router.get("/performance", response_class=HTMLResponse)
async def performance_page(request: Request, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return RedirectResponse("/ui/login", status_code=303)
    trades = svc.paper_performance()
    summary = svc.performance_summary()
    return templates.TemplateResponse("performance.html", {
        "request": request,
        "page": "performance",
        "trades": trades,
        "summary": summary,
    })


# ------------------------------------------------------------------
# Pipeline
# ------------------------------------------------------------------

@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(request: Request, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return RedirectResponse("/ui/login", status_code=303)
    health = svc.pipeline_health()
    poll_runs = svc.recent_poll_runs(limit=20)
    processing = svc.recent_processing(limit=10)
    return templates.TemplateResponse("pipeline.html", {
        "request": request,
        "page": "pipeline",
        "health": health,
        "poll_runs": poll_runs,
        "processing": processing,
    })


@router.get("/partials/pipeline-health", response_class=HTMLResponse)
async def pipeline_health_partial(request: Request, svc: UIService = Depends(_svc)):
    if not _guard(request):
        return HTMLResponse("", status_code=401)
    health = svc.pipeline_health()
    return templates.TemplateResponse("partials/pipeline_health.html", {
        "request": request,
        "health": health,
    })
