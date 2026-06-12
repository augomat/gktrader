from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from gktrader.db.models import Position, PositionEvent
from gktrader.domain.enums import Direction, PositionEventType


@dataclass
class PositionState:
    direction: Direction
    net_amount_eur: float
    average_price: float | None
    updated_at: datetime


def apply_position_event(position: Position | None, event: PositionEvent) -> PositionState:
    current_amount = position.net_amount_eur if position else 0.0
    current_price = position.average_price if position else None
    amount = event.amount_eur or 0.0

    if event.event_type in {PositionEventType.OPEN, PositionEventType.INCREASE, PositionEventType.ADJUST}:
        next_amount = amount if event.event_type == PositionEventType.ADJUST else current_amount + amount
    elif event.event_type == PositionEventType.REDUCE:
        next_amount = max(0.0, current_amount - amount)
    elif event.event_type == PositionEventType.CLOSE:
        next_amount = 0.0
    else:
        next_amount = current_amount

    if next_amount == 0:
        next_price = None
    elif event.price and amount and current_amount > 0 and current_price:
        next_price = ((current_amount * current_price) + (amount * event.price)) / (current_amount + amount)
    else:
        next_price = event.price or current_price

    return PositionState(
        direction=event.direction,
        net_amount_eur=next_amount,
        average_price=next_price,
        updated_at=datetime.now(UTC),
    )
