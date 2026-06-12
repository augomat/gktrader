"""Tests for catalyst scoring and actionability rules."""

from __future__ import annotations

from gktrader.domain.enums import AlertLevel, Direction, EventType
from gktrader.intelligence.scoring import (
    ScoreContext,
    compute_actionability,
    compute_catalyst_score,
    compute_modifiers,
    get_base_catalyst_score,
)


class TestBaseCatalystScores:
    """Base score by event type."""

    def test_equity_stake_score(self) -> None:
        assert get_base_catalyst_score(EventType.GOVERNMENT_EQUITY_STAKE.value) == 5

    def test_funding_score(self) -> None:
        assert get_base_catalyst_score(EventType.GOVERNMENT_FUNDING.value) == 5

    def test_contract_score(self) -> None:
        assert get_base_catalyst_score(EventType.GOVERNMENT_CONTRACT.value) == 5

    def test_regulatory_score(self) -> None:
        assert get_base_catalyst_score(EventType.REGULATORY_TAILWIND.value) == 4
        assert get_base_catalyst_score(EventType.REGULATORY_HEADWIND.value) == 4

    def test_presidential_mention_score(self) -> None:
        assert get_base_catalyst_score(EventType.PRESIDENTIAL_POSITIVE_MENTION.value) == 3
        assert get_base_catalyst_score(EventType.PRESIDENTIAL_NEGATIVE_MENTION.value) == 3

    def test_oge_disclosure_score(self) -> None:
        assert get_base_catalyst_score(EventType.OGE_PURCHASE_DISCLOSURE.value) == 3
        assert get_base_catalyst_score(EventType.OGE_SALE_DISCLOSURE.value) == 3

    def test_8k_score(self) -> None:
        assert get_base_catalyst_score(EventType.COMPANY_CONFIRMATION_8K.value) == 2

    def test_sector_only_score(self) -> None:
        assert get_base_catalyst_score(EventType.SECTOR_ONLY_MENTION.value) == 1

    def test_irrelevant_score(self) -> None:
        assert get_base_catalyst_score(EventType.IRRELEVANT.value) == 0

    def test_unknown_type_score(self) -> None:
        assert get_base_catalyst_score("unknown_type") == 0


class TestComputeModifiers:
    """Modifier computation."""

    def test_multiple_sources_plus_one(self) -> None:
        ctx = ScoreContext(
            event_type="government_funding",
            direction="bullish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=1.0,
            has_multiple_sources=True,
        )
        mods = compute_modifiers(ctx)
        assert any(m.delta == 1 and "Multiple independent sources" in m.reason for m in mods)

    def test_official_confirmation_plus_one(self) -> None:
        ctx = ScoreContext(
            event_type="government_funding",
            direction="bullish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=1.0,
            has_direct_official_confirmation=True,
        )
        mods = compute_modifiers(ctx)
        assert any(m.delta == 1 and "concrete details" in m.reason for m in mods)

    def test_low_mapping_confidence_minus_one(self) -> None:
        ctx = ScoreContext(
            event_type="government_funding",
            direction="bullish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=0.80,
        )
        mods = compute_modifiers(ctx)
        assert any(m.delta == -1 and "Mapping confidence below 0.90" in m.reason for m in mods)

    def test_stale_event_minus_one(self) -> None:
        ctx = ScoreContext(
            event_type="government_funding",
            direction="bullish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=1.0,
            is_stale=True,
        )
        mods = compute_modifiers(ctx)
        assert any(m.delta == -1 and "Stale or recycled" in m.reason for m in mods)

    def test_secondary_source_minus_two(self) -> None:
        ctx = ScoreContext(
            event_type="government_funding",
            direction="bullish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=1.0,
            is_secondary_source=True,
        )
        mods = compute_modifiers(ctx)
        assert any(m.delta == -2 and "Secondary source" in m.reason for m in mods)

    def test_no_stock_move_modifiers(self) -> None:
        """Stock-move penalties are removed; price-move downgrades are
        handled in _determine_alert_level, not as score modifiers."""
        ctx = ScoreContext(
            event_type="government_funding",
            direction="bullish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=1.0,
            intraday_move_pct=45.0,
        )
        mods = compute_modifiers(ctx)
        # No modifier should reference "Stock moved" or price moves
        assert not any("Stock moved" in m.reason for m in mods)
        assert not any("intraday" in m.reason.lower() for m in mods)


class TestComputeActionability:
    """Actionability and alert level determination."""

    def _make_context(
        self,
        event_type: str = "government_funding",
        direction: str = "bullish",
        strength: int = 5,
        classifier_confidence: float = 0.90,
        mapping_confidence: float = 1.0,
        source_tier: str = "tier_1",
        active_public_ticker: bool = True,
        market_snapshot_available: bool = True,
        intraday_move_pct: float | None = None,
        is_stale: bool = False,
    ) -> ScoreContext:
        return ScoreContext(
            event_type=event_type,
            direction=direction,
            strength=strength,
            classifier_confidence=classifier_confidence,
            mapping_confidence=mapping_confidence,
            source_tier=source_tier,
            active_public_ticker=active_public_ticker,
            market_snapshot_available=market_snapshot_available,
            intraday_move_pct=intraday_move_pct,
            is_stale=is_stale,
        )

    def test_strong_event_passes_tradeable_gate(self) -> None:
        ctx = self._make_context()
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.TRADEABLE
        assert decision.catalyst_score >= 5

    def test_low_mapping_confidence_downgrades_to_review(self) -> None:
        """Mapping confidence below 0.90 can never become TRADEABLE."""
        ctx = self._make_context(mapping_confidence=0.80)
        decision = compute_actionability(ctx)
        assert decision.alert_level != AlertLevel.TRADEABLE
        # Should be REVIEW or lower
        assert decision.alert_level in (AlertLevel.REVIEW, AlertLevel.WATCH, AlertLevel.IGNORE)

    def test_low_classifier_confidence_downgrades(self) -> None:
        ctx = self._make_context(classifier_confidence=0.70)
        decision = compute_actionability(ctx)
        assert decision.alert_level != AlertLevel.TRADEABLE

    def test_no_market_data_downgrades_to_review(self) -> None:
        """Missing market data forces to REVIEW even for strong events."""
        ctx = self._make_context(market_snapshot_available=False)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.REVIEW

    def test_strong_event_after_25_pct_move_becomes_review(self) -> None:
        ctx = self._make_context(intraday_move_pct=25.0)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.REVIEW

    def test_strong_event_after_45_pct_move_becomes_avoid_chase(self) -> None:
        ctx = self._make_context(intraday_move_pct=45.0)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.AVOID_CHASE

    def test_bearish_negative_5_pct_retains_tradeable(self) -> None:
        """Bearish event with -5% move (abs < 10%) retains TRADEABLE."""
        ctx = self._make_context(direction="bearish", intraday_move_pct=-5.0)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.TRADEABLE

    def test_bearish_negative_15_pct_downgrades_to_review(self) -> None:
        """Bearish event with -15% move (abs 10%–25%) downgrades to REVIEW."""
        ctx = self._make_context(direction="bearish", intraday_move_pct=-15.0)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.REVIEW

    def test_bearish_negative_30_pct_downgrades_to_avoid_chase(self) -> None:
        """Bearish event with -30% move (abs > 25%) downgrades to AVOID_CHASE."""
        ctx = self._make_context(direction="bearish", intraday_move_pct=-30.0)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.AVOID_CHASE

    def test_exactly_10_pct_downgrades_to_review(self) -> None:
        """At exactly +10%: downgrade to REVIEW."""
        ctx = self._make_context(intraday_move_pct=10.0)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.REVIEW

    def test_exactly_25_pct_downgrades_to_review(self) -> None:
        """At exactly +25%: downgrade to REVIEW (upper bound of review range)."""
        ctx = self._make_context(intraday_move_pct=25.0)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.REVIEW

    def test_bearish_event_tradeable(self) -> None:
        ctx = self._make_context(direction="bearish")
        decision = compute_actionability(ctx)
        assert decision.direction == Direction.BEARISH

    def test_neutral_event_not_tradeable(self) -> None:
        ctx = self._make_context(direction="neutral")
        decision = compute_actionability(ctx)
        assert decision.alert_level != AlertLevel.TRADEABLE

    def test_irrelevant_event_is_ignored(self) -> None:
        ctx = self._make_context(event_type="irrelevant")
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.IGNORE

    def test_no_active_ticker_is_ignored(self) -> None:
        ctx = self._make_context(active_public_ticker=False)
        decision = compute_actionability(ctx)
        assert decision.alert_level == AlertLevel.IGNORE

    def test_tier_2_source_not_tradeable(self) -> None:
        ctx = self._make_context(source_tier="tier_2")
        decision = compute_actionability(ctx)
        assert decision.alert_level != AlertLevel.TRADEABLE

    def test_stale_event_not_tradeable(self) -> None:
        ctx = self._make_context(is_stale=True)
        decision = compute_actionability(ctx)
        assert decision.alert_level != AlertLevel.TRADEABLE

    def test_market_data_cannot_promote(self) -> None:
        """Market data may only downgrade, never promote."""
        ctx = self._make_context(
            mapping_confidence=0.80,  # Below TRADEABLE threshold
            classifier_confidence=0.90,
            market_snapshot_available=True,
            intraday_move_pct=0.0,  # No move at all
        )
        decision = compute_actionability(ctx)
        # Even though market data is perfect, low mapping confidence
        # means it should not be TRADEABLE
        assert decision.alert_level != AlertLevel.TRADEABLE


class TestNegativeContextCases:
    """Negative context handling."""

    def test_negative_mention_gets_base_score(self) -> None:
        """Negative mentions should still get appropriate base score."""
        ctx = ScoreContext(
            event_type="presidential_negative_mention",
            direction="bearish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=1.0,
            source_tier="tier_1",
            active_public_ticker=True,
            market_snapshot_available=True,
        )
        score = compute_catalyst_score(ctx)
        assert score >= 3  # presidential mentions have base score 3

    def test_negative_context_actionability(self) -> None:
        """Negative context events should get proper actionability."""
        ctx = ScoreContext(
            event_type="presidential_negative_mention",
            direction="bearish",
            strength=5,
            classifier_confidence=0.90,
            mapping_confidence=1.0,
            source_tier="tier_1",
            active_public_ticker=True,
            market_snapshot_available=True,
        )
        decision = compute_actionability(ctx)
        assert decision.direction == Direction.BEARISH
        # A strong negative mention from a tier 1 source can be tradeable
        assert decision.alert_level == AlertLevel.TRADEABLE

    def test_regulatory_headwind_scoring(self) -> None:
        """Regulatory headwinds should score similarly to tailwinds."""
        ctx = ScoreContext(
            event_type="regulatory_headwind",
            direction="bearish",
            strength=4,
            classifier_confidence=0.85,
            mapping_confidence=1.0,
        )
        base = compute_catalyst_score(ctx)
        assert base >= 4
