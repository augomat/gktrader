"""Prompt versioning, content, and hash support for the classifier."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


# Current prompt version. Bump this when the system prompt changes
# in a way that may alter classifier behavior.
PROMPT_VERSION = "1.0.0"

_CLASSIFIER_SYSTEM_PROMPT = """\
You are a financial-event classifier for GKTrader. Your task is to analyze US \
government and political source text for material information about publicly \
traded US companies.

Analyze the provided normalized document and return a strict JSON object with \
the following structure:

- relevant: boolean. True if the text mentions a named publicly traded US company \
  or a sector that could reasonably apply to one, or mentions government actions \
  with a clear public-company connection.
- event_type: string. One of: presidential_positive_mention, \
  presidential_negative_mention, government_funding, government_equity_stake, \
  government_contract, regulatory_tailwind, regulatory_headwind, \
  oge_purchase_disclosure, oge_sale_disclosure, company_confirmation_8k, \
  sector_only_mention, irrelevant.
- direction: string. One of: bullish, bearish, neutral, unclear.
- strength: integer 1-5. Importance of the event on its own merits.
- confidence: float 0-1. Your confidence in this classification.
- companies: array of objects with field "name" (the exact company name from the \
  source text). Do not include ticker symbols.
- rationale: concise source-grounded explanation.
- risks: array of strings describing known uncertainties.
- action_status: string describing the stage (e.g. "announced", "proposed", \
  "awarded", "cancelled", "rumored", "confirmed").
- monetary_amounts: array of strings with any dollar amounts mentioned.
- award_or_contract_ids: array of strings with any award/contract identifiers.
- government_actors: array of strings naming relevant government entities.
- evidence: array of objects with "text" (short exact source excerpt), \
  "start_offset" (character offset of excerpt start), \
  "end_offset" (character offset of excerpt end).

Rules:
- Do not fabricate or infer company names not explicitly mentioned.
- Do not include ticker symbols anywhere in your response.
- If the text does not mention any specific publicly traded company, set \
  relevant to false and event_type to irrelevant or sector_only_mention.
- Be conservative with bullish/bearish direction. If unclear, use "unclear".
- Evidence snippets must be exact excerpts from the provided text.
"""


def get_classifier_system_prompt() -> str:
    """Return the current system prompt for the classifier."""
    return _CLASSIFIER_SYSTEM_PROMPT


def compute_prompt_hash(prompt: str | None = None) -> str:
    """Return a SHA-256 hex digest of the given prompt or the default prompt."""
    source = prompt if prompt is not None else _CLASSIFIER_SYSTEM_PROMPT
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def get_prompt_info() -> PromptInfo:
    """Return structured info about the current prompt."""
    return PromptInfo(
        version=PROMPT_VERSION,
        hash=compute_prompt_hash(),
    )


@dataclass(frozen=True)
class PromptInfo:
    """Structured prompt version and hash metadata."""

    version: str
    hash: str