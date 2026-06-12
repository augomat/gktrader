from datetime import UTC, datetime

import pytest

from gktrader.domain.contracts import ClassifierResult, EvidenceSnippet


def test_classifier_contract_accepts_strict_schema() -> None:
    result = ClassifierResult.model_validate(
        {
            "relevant": True,
            "event_type": "government_funding",
            "direction": "bullish",
            "strength": 5,
            "confidence": 0.88,
            "companies": [{"name": "Example Corporation"}],
            "rationale": "Concise source-grounded explanation.",
            "risks": ["Known uncertainty"],
            "action_status": "announced",
            "monetary_amounts": [],
            "award_or_contract_ids": [],
            "government_actors": [],
            "evidence": [{"text": "Short excerpt", "start_offset": 1, "end_offset": 12}],
        }
    )

    assert result.event_type == "government_funding"
    assert result.evidence[0].text == "Short excerpt"


def test_evidence_offsets_fail_closed() -> None:
    with pytest.raises(ValueError):
        EvidenceSnippet(text="x", start_offset=10, end_offset=10)
