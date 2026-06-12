from gktrader.db.models import Position, PositionEvent
from gktrader.domain.enums import Direction, PositionEventType
from gktrader.reporting.positions import apply_position_event


def test_apply_position_event_projects_state() -> None:
    opened = PositionEvent(
        ticker="RGTI",
        event_type=PositionEventType.OPEN,
        direction=Direction.BULLISH,
        amount_eur=1000,
        price=4.0,
    )

    state = apply_position_event(None, opened)

    assert state.net_amount_eur == 1000
    assert state.average_price == 4.0


def test_confirm_event_preserves_position_amount() -> None:
    """CONFIRM should append without changing amount."""
    position = Position(
        ticker="RGTI",
        direction=Direction.BULLISH,
        net_amount_eur=1000.0,
        average_price=4.0,
    )
    event = PositionEvent(
        ticker="RGTI",
        event_type=PositionEventType.CONFIRM,
        direction=Direction.BULLISH,
    )
    state = apply_position_event(position, event)
    assert state.net_amount_eur == 1000.0


def test_close_event_sets_amount_to_zero() -> None:
    position = Position(
        ticker="RGTI",
        direction=Direction.BULLISH,
        net_amount_eur=1000.0,
        average_price=4.0,
    )
    event = PositionEvent(
        ticker="RGTI",
        event_type=PositionEventType.CLOSE,
        direction=Direction.BULLISH,
        amount_eur=0.0,
    )
    state = apply_position_event(position, event)
    assert state.net_amount_eur == 0.0
    assert state.average_price is None


def test_adjust_event_replaces_amount() -> None:
    position = Position(
        ticker="RGTI",
        direction=Direction.BULLISH,
        net_amount_eur=1000.0,
        average_price=4.0,
    )
    event = PositionEvent(
        ticker="RGTI",
        event_type=PositionEventType.ADJUST,
        direction=Direction.BULLISH,
        amount_eur=500.0,
        price=5.0,
    )
    state = apply_position_event(position, event)
    assert state.net_amount_eur == 500.0
    # Price is weighted average of old and new
    expected_price = ((1000.0 * 4.0) + (500.0 * 5.0)) / (1000.0 + 500.0)
    assert state.average_price == expected_price


def test_open_then_close_produces_zero_flat() -> None:
    """Open then close should result in zero amount."""
    open_event = PositionEvent(
        ticker="MU",
        event_type=PositionEventType.OPEN,
        direction=Direction.BEARISH,
        amount_eur=1000.0,
        price=50.0,
    )
    state = apply_position_event(None, open_event)
    assert state.net_amount_eur == 1000.0

    position = Position(
        ticker="MU",
        direction=Direction.BEARISH,
        net_amount_eur=state.net_amount_eur,
        average_price=state.average_price,
    )
    close_event = PositionEvent(
        ticker="MU",
        event_type=PositionEventType.CLOSE,
        direction=Direction.BEARISH,
        amount_eur=0.0,
    )
    final = apply_position_event(position, close_event)
    assert final.net_amount_eur == 0.0
