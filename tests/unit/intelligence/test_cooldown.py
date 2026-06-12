"""Tests for cooldown and material-update logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gktrader.intelligence.cooldown import (
    CooldownKey,
    CooldownState,
    MaterialUpdateCheck,
    _level_rank,
    is_material_update,
    is_on_cooldown,
)


class TestCooldownKey:
    """Cooldown key string representation."""

    def test_key_str(self) -> None:
        key = CooldownKey(ticker="RGTI", event_type="government_funding", direction="bullish")
        assert str(key) == "RGTI:government_funding:bullish"

    def test_key_str_lowercase_ticker(self) -> None:
        key = CooldownKey(ticker="rgti", event_type="government_funding", direction="bullish")
        assert str(key) == "RGTI:government_funding:bullish"


class TestIsOnCooldown:
    """Cooldown state checking."""

    def test_no_prior_state(self) -> None:
        assert is_on_cooldown(None) is False

    def test_on_cooldown(self) -> None:
        state = CooldownState(
            key=CooldownKey(ticker="RGTI", event_type="government_funding", direction="bullish"),
            last_alerted_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert is_on_cooldown(state) is True

    def test_cooldown_expired(self) -> None:
        state = CooldownState(
            key=CooldownKey(ticker="RGTI", event_type="government_funding", direction="bullish"),
            last_alerted_at=datetime.now(timezone.utc) - timedelta(hours=7),
        )
        assert is_on_cooldown(state) is False

    def test_expires_at_calculation(self) -> None:
        now = datetime.now(timezone.utc)
        state = CooldownState(
            key=CooldownKey(ticker="RGTI", event_type="government_funding", direction="bullish"),
            last_alerted_at=now,
        )
        expected = now + timedelta(hours=6)
        assert abs((state.expires_at - expected).total_seconds()) < 1

    def test_remaining_seconds(self) -> None:
        state = CooldownState(
            key=CooldownKey(ticker="RGTI", event_type="government_funding", direction="bullish"),
            last_alerted_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        remaining = state.remaining_seconds
        assert remaining > 0
        assert remaining < 6 * 3600

    def test_remaining_seconds_expired(self) -> None:
        state = CooldownState(
            key=CooldownKey(ticker="RGTI", event_type="government_funding", direction="bullish"),
            last_alerted_at=datetime.now(timezone.utc) - timedelta(hours=7),
        )
        assert state.remaining_seconds == 0.0


class TestIsMaterialUpdate:
    """Material update detection."""

    def test_no_change_not_material(self) -> None:
        previous = {
            "direction": "bullish",
            "action_status": "announced",
            "source_names": ["whitehouse"],
            "award_or_contract_ids": ["ABC-123"],
            "monetary_amounts": ["$15M"],
            "catalyst_score": 5,
            "alert_level": "TRADEABLE",
        }
        new = dict(previous)
        result = is_material_update(previous, new)
        assert not result.is_material
        assert len(result.reasons) == 0

    def test_direction_change_is_material(self) -> None:
        previous = {"direction": "bullish"}
        new = {"direction": "bearish"}
        result = is_material_update(previous, new)
        assert result.is_material
        assert any("Direction changed" in r for r in result.reasons)

    def test_action_status_change_is_material(self) -> None:
        previous = {"direction": "bullish", "action_status": "proposed"}
        new = {"direction": "bullish", "action_status": "awarded"}
        result = is_material_update(previous, new)
        assert result.is_material
        assert any("Action status changed" in r for r in result.reasons)

    def test_new_source_is_material(self) -> None:
        previous = {"direction": "bullish", "source_names": ["whitehouse"]}
        new = {"direction": "bullish", "source_names": ["whitehouse", "sec"]}
        result = is_material_update(previous, new)
        assert result.is_material
        assert any("New source" in r for r in result.reasons)

    def test_new_award_id_is_material(self) -> None:
        previous = {"direction": "bullish", "award_or_contract_ids": ["ABC-123"]}
        new = {"direction": "bullish", "award_or_contract_ids": ["ABC-123", "XYZ-789"]}
        result = is_material_update(previous, new)
        assert result.is_material
        assert any("New award/contract IDs" in r for r in result.reasons)

    def test_new_monetary_amount_is_material(self) -> None:
        previous = {"direction": "bullish", "monetary_amounts": ["$15M"]}
        new = {"direction": "bullish", "monetary_amounts": ["$15M", "$30M"]}
        result = is_material_update(previous, new)
        assert result.is_material
        assert any("New monetary amounts" in r for r in result.reasons)

    def test_catalyst_score_increase_is_material(self) -> None:
        previous = {"direction": "bullish", "catalyst_score": 5}
        new = {"direction": "bullish", "catalyst_score": 6}
        result = is_material_update(previous, new)
        assert result.is_material
        assert any("Catalyst score increased" in r for r in result.reasons)

    def test_alert_level_increase_is_material(self) -> None:
        previous = {"direction": "bullish", "alert_level": "REVIEW"}
        new = {"direction": "bullish", "alert_level": "TRADEABLE"}
        result = is_material_update(previous, new)
        assert result.is_material
        assert any("Alert level increased" in r for r in result.reasons)

    def test_multiple_changes_accumulate_reasons(self) -> None:
        previous = {
            "direction": "bullish",
            "action_status": "announced",
            "source_names": ["source_a"],
            "catalyst_score": 4,
            "alert_level": "REVIEW",
        }
        new = {
            "direction": "bearish",
            "action_status": "cancelled",
            "source_names": ["source_a", "source_b"],
            "catalyst_score": 6,
            "alert_level": "TRADEABLE",
        }
        result = is_material_update(previous, new)
        assert result.is_material
        assert len(result.reasons) >= 3

    def test_empty_dicts_not_material(self) -> None:
        result = is_material_update({}, {})
        assert not result.is_material

    def test_none_values_handled(self) -> None:
        previous = {"direction": None, "source_names": None}
        new = {"direction": "bullish", "source_names": ["sec"]}
        result = is_material_update(previous, new)
        assert result.is_material


class TestLevelRank:
    """Bug #4: AVOID_CHASE must rank below TRADEABLE (price-driven downgrade, not promotion)."""

    def test_avoid_chase_below_tradeable(self) -> None:
        assert _level_rank("AVOID_CHASE") < _level_rank("TRADEABLE"), (
            "AVOID_CHASE is a downgrade from TRADEABLE; it must rank lower"
        )

    def test_tradeable_to_avoid_chase_not_material_upgrade(self) -> None:
        previous = {"alert_level": "TRADEABLE", "catalyst_score": 5}
        new = {"alert_level": "AVOID_CHASE", "catalyst_score": 5}
        result = is_material_update(previous, new)
        # TRADEABLE→AVOID_CHASE should NOT count as a level increase
        assert not any("level increased" in r.lower() for r in result.reasons), (
            "TRADEABLE→AVOID_CHASE must not trigger a material-update via level increase"
        )

    def test_review_to_tradeable_is_level_increase(self) -> None:
        previous = {"alert_level": "REVIEW", "catalyst_score": 3}
        new = {"alert_level": "TRADEABLE", "catalyst_score": 3}
        result = is_material_update(previous, new)
        assert result.is_material, "REVIEW→TRADEABLE should be a material level increase"