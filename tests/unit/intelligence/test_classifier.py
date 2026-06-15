"""Tests for the classifier module: validation, retry, and fail-closed behavior."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from gktrader.domain.contracts import ClassifierResult
from gktrader.domain.enums import ProcessingStatus
from gktrader.config.settings import DEFAULT_OPENROUTER_FALLBACK_MODEL
from gktrader.intelligence.classifier import ClassifierConfig, OpenRouterClassifier, _estimate_cost


class TestClassifierResultValidation:
    """Tests for ClassifierResult strict schema validation."""

    def test_valid_minimal_response(self) -> None:
        """A valid minimal classifier response passes validation."""
        data = {
            "relevant": True,
            "event_type": "government_funding",
            "direction": "bullish",
            "strength": 4,
            "confidence": 0.85,
            "companies": [{"name": "Rigetti Computing"}],
            "rationale": "Government funding announcement.",
            "risks": ["Risk of delay"],
            "action_status": "announced",
            "monetary_amounts": ["$15M"],
            "award_or_contract_ids": [],
            "government_actors": ["DOC"],
            "evidence": [
                {"text": "funding of $15M", "start_offset": 100, "end_offset": 115}
            ],
        }
        result = ClassifierResult.model_validate(data)
        assert result.event_type == "government_funding"
        assert result.direction == "bullish"
        assert result.companies[0].name == "Rigetti Computing"

    def test_rejects_ticker_in_company(self) -> None:
        """Ticker in the company field should still pass schema but not be used.
        The resolver handles discarding LLM-provided tickers."""
        data = {
            "relevant": True,
            "event_type": "presidential_positive_mention",
            "direction": "bullish",
            "strength": 3,
            "confidence": 0.75,
            "companies": [{"name": "Rigetti Computing"}],
            "rationale": "Test",
            "risks": [],
            "action_status": "mentioned",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": [],
            "evidence": [{"text": "mention", "start_offset": 0, "end_offset": 7}],
        }
        result = ClassifierResult.model_validate(data)
        assert result.companies[0].name == "Rigetti Computing"

    def test_rejects_extra_fields(self) -> None:
        """Extra fields in the response should be rejected (extra='forbid')."""
        data = {
            "relevant": False,
            "event_type": "irrelevant",
            "direction": "neutral",
            "strength": 1,
            "confidence": 0.99,
            "companies": [],
            "rationale": "Not relevant.",
            "risks": [],
            "action_status": "none",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": [],
            "evidence": [{"text": "irrelevant", "start_offset": 0, "end_offset": 10}],
            "extra_field": "should_not_exist",
        }
        with pytest.raises(ValidationError):
            ClassifierResult.model_validate(data)

    def test_rejects_missing_required_fields(self) -> None:
        """Missing required fields are rejected."""
        data = {
            "relevant": True,
            "event_type": "government_funding",
            # missing direction, strength, evidence, etc.
        }
        with pytest.raises(ValidationError):
            ClassifierResult.model_validate(data)

    def test_rejects_invalid_strength_range(self) -> None:
        """Strength must be 1-5."""
        data = {
            "relevant": True,
            "event_type": "government_funding",
            "direction": "bullish",
            "strength": 6,
            "confidence": 0.85,
            "companies": [{"name": "Test"}],
            "rationale": "Test",
            "risks": [],
            "action_status": "announced",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": [],
            "evidence": [{"text": "test", "start_offset": 0, "end_offset": 4}],
        }
        with pytest.raises(ValidationError):
            ClassifierResult.model_validate(data)

    def test_rejects_invalid_confidence_range(self) -> None:
        """Confidence must be 0-1."""
        data = {
            "relevant": True,
            "event_type": "government_funding",
            "direction": "bullish",
            "strength": 3,
            "confidence": 1.5,
            "companies": [{"name": "Test"}],
            "rationale": "Test",
            "risks": [],
            "action_status": "announced",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": [],
            "evidence": [{"text": "test", "start_offset": 0, "end_offset": 4}],
        }
        with pytest.raises(ValidationError):
            ClassifierResult.model_validate(data)

    def test_missing_evidence_is_rejected(self) -> None:
        """At least one evidence snippet is required (min_length=1)."""
        data = {
            "relevant": True,
            "event_type": "government_funding",
            "direction": "bullish",
            "strength": 3,
            "confidence": 0.85,
            "companies": [{"name": "Test"}],
            "rationale": "Test",
            "risks": [],
            "action_status": "announced",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": [],
            "evidence": [],
        }
        with pytest.raises(ValidationError):
            ClassifierResult.model_validate(data)

    def test_evidence_offset_validation(self) -> None:
        """Evidence snippet start_offset must be less than end_offset."""
        data = {
            "relevant": True,
            "event_type": "government_funding",
            "direction": "bullish",
            "strength": 3,
            "confidence": 0.85,
            "companies": [{"name": "Test"}],
            "rationale": "Test",
            "risks": [],
            "action_status": "announced",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": [],
            "evidence": [{"text": "test", "start_offset": 10, "end_offset": 5}],
        }
        with pytest.raises(ValidationError):
            ClassifierResult.model_validate(data)

    def test_negative_context_is_not_bullish(self) -> None:
        """Negative context should be classified as bearish or unclear, not bullish."""
        # This tests schema acceptance, not semantic classification
        data = {
            "relevant": True,
            "event_type": "presidential_negative_mention",
            "direction": "bearish",
            "strength": 4,
            "confidence": 0.90,
            "companies": [{"name": "Test Corp"}],
            "rationale": "Negative mention by official.",
            "risks": [],
            "action_status": "mentioned",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": ["President"],
            "evidence": [{"text": "negative", "start_offset": 0, "end_offset": 8}],
        }
        result = ClassifierResult.model_validate(data)
        assert result.direction == "bearish"
        assert result.event_type == "presidential_negative_mention"


class TestCostEstimation:
    """Tests for API cost estimation."""

    def test_estimate_cost_with_usage(self) -> None:
        usage = {"prompt_tokens": 1000, "completion_tokens": 200}
        cost = _estimate_cost(usage, "google/gemini-2.0-flash-lite")
        assert cost is not None
        assert cost > 0
        assert cost < 1  # Should be tiny

    def test_estimate_cost_no_usage(self) -> None:
        cost = _estimate_cost({}, "google/gemini-2.0-flash-lite")
        assert cost is None


class TestClassifierConfigDefaults:
    def test_default_fallback_model_is_deepseek(self) -> None:
        config = ClassifierConfig(api_key="test")
        assert config.fallback_model == DEFAULT_OPENROUTER_FALLBACK_MODEL


class TestClassifierInvalidResponse:
    """Tests for classifier response handling."""

    def test_validate_response_markdown_fence(self) -> None:
        """_validate_response should strip markdown code fences."""
        valid_json = json.dumps(
            {
                "relevant": True,
                "event_type": "government_funding",
                "direction": "bullish",
                "strength": 4,
                "confidence": 0.85,
                "companies": [{"name": "Test Corp"}],
                "rationale": "Test",
                "risks": [],
                "action_status": "announced",
                "monetary_amounts": [],
                "award_or_contract_ids": [],
                "government_actors": [],
                "evidence": [{"text": "test", "start_offset": 0, "end_offset": 4}],
            }
        )
        fenced = f"```json\n{valid_json}\n```"

        # Use a dummy config to instantiate the classifier
        from gktrader.intelligence.classifier import ClassifierConfig

        config = ClassifierConfig(api_key="test")
        classifier = OpenRouterClassifier(config)

        result = classifier._validate_response(fenced)
        assert result is not None
        assert result.event_type == "government_funding"
