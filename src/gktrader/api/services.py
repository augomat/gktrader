from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from gktrader.config.settings import get_settings
from gktrader.db.models import (
    Alert,
    InteractionState,
    Position,
    PositionEvent,
    SignalEvent,
    TradeDecision,
    WeeklyReport,
)
from gktrader.domain.contracts import (
    AlertDecisionRequest,
    AlertDecisionResponse,
    CompanyHistoryResponse,
    PositionConfirmationRequest,
    PositionEventRequest,
    PositionSummary,
    PriorBullishSignal,
    WeeklyReviewPosition,
    WeeklyReviewResponse,
)
from gktrader.domain.enums import Direction, InteractionStateType, PositionEventType, TradeDecisionType
from gktrader.reporting.positions import apply_position_event


class ApiService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def recent_alerts(self) -> list[dict]:
        rows = self.db.scalars(select(Alert).order_by(desc(Alert.created_at)).limit(20)).all()
        return [{"id": row.id, "level": row.level.value, "payload": row.rendered_payload} for row in rows]

    def get_alert(self, alert_id: str) -> dict:
        alert = self.db.get(Alert, alert_id)
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
        return {
            "id": alert.id,
            "level": alert.level.value,
            "payload": alert.rendered_payload,
            "created_at": alert.created_at,
        }

    def record_alert_decision(
        self,
        alert_id: str,
        payload: AlertDecisionRequest,
        idempotency_key: str,
    ) -> AlertDecisionResponse:
        existing = self.db.scalar(
            select(TradeDecision).where(TradeDecision.idempotency_key == idempotency_key)
        )
        if existing:
            return AlertDecisionResponse(
                alert_id=existing.alert_id,
                decision_id=existing.id,
                position_event_id=None,
            )

        alert = self.db.get(Alert, alert_id)
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

        decision = TradeDecision(
            alert_id=alert_id,
            decision=payload.decision,
            amount_eur=payload.amount_eur,
            execution_price=payload.execution_price,
            notes=payload.notes,
            idempotency_key=idempotency_key,
        )
        self.db.add(decision)

        position_event_id = None
        if payload.decision in {
            TradeDecisionType.BOUGHT,
            TradeDecisionType.SHORTED,
            TradeDecisionType.SOLD_REDUCED,
        }:
            rendered = alert.rendered_payload or {}
            ticker = rendered.get("ticker")
            if not ticker:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Alert is missing ticker context",
                )
            position_event = PositionEvent(
                ticker=ticker,
                event_type=(
                    PositionEventType.OPEN
                    if payload.decision in {TradeDecisionType.BOUGHT, TradeDecisionType.SHORTED}
                    else PositionEventType.REDUCE
                ),
                direction=(
                    Direction.BEARISH if payload.decision == TradeDecisionType.SHORTED else Direction.BULLISH
                ),
                amount_eur=payload.amount_eur,
                price=payload.execution_price,
                notes=payload.notes,
                source_alert_id=alert_id,
            )
            self.db.add(position_event)
            self.db.flush()
            position_event_id = position_event.id
            self._upsert_position(position_event)

        self.db.commit()
        return AlertDecisionResponse(
            alert_id=alert_id,
            decision_id=decision.id,
            position_event_id=position_event_id,
        )

    def snooze_alert(self, alert_id: str, minutes: int, idempotency_key: str) -> dict:
        # Idempotency check: same idempotency_key should not create a duplicate
        existing = self.db.scalar(
            select(InteractionState).where(
                InteractionState.payload["idempotency_key"].as_string() == idempotency_key,
                InteractionState.state_type == InteractionStateType.SNOOZE_REMINDER,
            )
        )
        if existing:
            return {
                "alert_id": alert_id,
                "minutes": minutes,
                "idempotency_key": idempotency_key,
                "status": "already_scheduled",
            }

        alert = self.db.get(Alert, alert_id)
        if not alert:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

        settings = get_settings()
        due_at = datetime.now(UTC) + timedelta(minutes=minutes)

        reminder = InteractionState(
            owner_id=str(settings.telegram_owner_id),
            state_type=InteractionStateType.SNOOZE_REMINDER,
            payload={
                "alert_id": alert_id,
                "idempotency_key": idempotency_key,
                "minutes": minutes,
                "due_at": due_at.isoformat(),
            },
            expires_at=due_at,
        )
        self.db.add(reminder)
        self.db.commit()
        return {
            "alert_id": alert_id,
            "minutes": minutes,
            "idempotency_key": idempotency_key,
            "status": "scheduled",
            "reminder_id": reminder.id,
            "due_at": due_at.isoformat(),
        }

    def get_event(self, event_id: str) -> dict:
        event = self.db.get(SignalEvent, event_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        return {
            "id": event.id,
            "fingerprint": event.fingerprint,
            "direction": event.direction.value,
            "payload": event.payload,
        }

    def company_history(self, ticker: str) -> CompanyHistoryResponse:
        events = self.db.scalars(
            select(SignalEvent)
            .where(SignalEvent.payload["ticker"].as_string() == ticker)
            .where(SignalEvent.direction == Direction.BULLISH)
            .order_by(SignalEvent.created_at)
        ).all()
        return CompanyHistoryResponse(
            ticker=ticker,
            signals=[
                PriorBullishSignal(
                    source_date=event.created_at,
                    event_type=event.event_type,
                    alert_level=event.alert_level,
                    rationale=str((event.payload or {}).get("rationale", "")),
                )
                for event in events
            ],
        )

    def list_positions(self) -> list[PositionSummary]:
        rows = self.db.scalars(select(Position).order_by(Position.ticker)).all()
        return [
            PositionSummary(
                ticker=row.ticker,
                direction=row.direction,
                net_amount_eur=row.net_amount_eur,
                average_price=row.average_price,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def record_position_event(self, payload: PositionEventRequest, idempotency_key: str) -> dict:
        existing = self.db.scalar(
            select(TradeDecision).where(TradeDecision.idempotency_key == idempotency_key)
        )
        if existing:
            return {"status": "recorded", "idempotency_key": idempotency_key}

        event = PositionEvent(
            ticker=payload.ticker,
            event_type=payload.event_type,
            direction=Direction.BULLISH,
            amount_eur=payload.amount_eur,
            price=payload.price,
            notes=payload.notes,
        )
        self.db.add(event)
        self.db.flush()
        self._upsert_position(event)

        marker = TradeDecision(
            alert_id=None,
            decision=TradeDecisionType.NO_TRADE,
            amount_eur=payload.amount_eur,
            execution_price=payload.price,
            notes=f"position-event:{event.id}",
            idempotency_key=idempotency_key,
        )
        self.db.add(marker)
        self.db.commit()
        return {"status": "recorded", "position_event_id": event.id}

    def get_weekly_review(self, generated_at: datetime) -> WeeklyReviewResponse:
        rows = self.db.scalars(select(Position).order_by(Position.ticker)).all()
        summary = f"Weekly review generated at {generated_at.isoformat()} with {len(rows)} open positions."
        return WeeklyReviewResponse(
            generated_at=generated_at,
            summary=summary,
            positions=[
                WeeklyReviewPosition(
                    position_id=row.id,
                    ticker=row.ticker,
                    direction=row.direction,
                    net_amount_eur=row.net_amount_eur,
                    status="open" if row.net_amount_eur else "flat",
                )
                for row in rows
            ],
        )

    def confirm_position(
        self,
        position_id: str,
        payload: PositionConfirmationRequest,
        idempotency_key: str,
    ) -> dict:
        # Idempotency check via TradeDecision marker (same pattern as record_position_event)
        existing = self.db.scalar(
            select(TradeDecision).where(TradeDecision.idempotency_key == idempotency_key)
        )
        if existing:
            return {"status": "already_recorded", "idempotency_key": idempotency_key}

        position = self.db.get(Position, position_id)
        if not position:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")

        # Map confirmation action to immutable PositionEventType
        action_map: dict[str, PositionEventType] = {
            "keep_open": PositionEventType.CONFIRM,
            "close": PositionEventType.CLOSE,
            "adjust": PositionEventType.ADJUST,
        }
        event_type = action_map.get(payload.action, PositionEventType.CONFIRM)
        amount_eur = payload.amount_eur if payload.action == "adjust" else (0.0 if payload.action == "close" else None)

        # Append immutable PositionEvent
        event = PositionEvent(
            ticker=position.ticker,
            event_type=event_type,
            direction=position.direction,
            amount_eur=amount_eur,
            notes=f"weekly-confirm:{payload.action}",
        )
        self.db.add(event)
        self.db.flush()

        # Reproject position state (never directly mutate the Position row)
        state = apply_position_event(position, event)
        position.direction = state.direction
        position.net_amount_eur = state.net_amount_eur
        position.average_price = state.average_price
        position.updated_at = state.updated_at
        self.db.add(position)

        # Idempotency marker
        marker = TradeDecision(
            alert_id=None,
            decision=TradeDecisionType.NO_TRADE,
            amount_eur=amount_eur,
            notes=f"weekly-confirm:{position_id}:{payload.action}",
            idempotency_key=idempotency_key,
        )
        self.db.add(marker)

        # Persist the confirmation in weekly report log
        report = WeeklyReport(report_payload={
            "position_id": position_id,
            "action": payload.action,
            "amount_eur": payload.amount_eur,
            "position_event_id": event.id,
        })
        self.db.add(report)
        self.db.commit()
        return {"status": "recorded", "position_event_id": event.id, "idempotency_key": idempotency_key}

    def _upsert_position(self, position_event: PositionEvent) -> None:
        position = self.db.scalar(select(Position).where(Position.ticker == position_event.ticker))
        state = apply_position_event(position, position_event)
        if position is None:
            position = Position(
                ticker=position_event.ticker,
                direction=state.direction,
                net_amount_eur=state.net_amount_eur,
                average_price=state.average_price,
                updated_at=state.updated_at,
            )
            self.db.add(position)
        else:
            position.direction = state.direction
            position.net_amount_eur = state.net_amount_eur
            position.average_price = state.average_price
            position.updated_at = state.updated_at
            self.db.add(position)
