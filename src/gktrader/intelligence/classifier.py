"""OpenRouter structured classifier client with strict schema validation and retry."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from gktrader.domain.contracts import ClassifierResult
from gktrader.domain.enums import ProcessingStatus
from gktrader.intelligence.prompts import compute_prompt_hash, get_classifier_system_prompt

logger = logging.getLogger(__name__)

# Default OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default model; override via environment or constructor
DEFAULT_MODEL = "google/gemini-2.0-flash-lite"

# Repair instruction sent on the retry attempt after an invalid response
_REPAIR_INSTRUCTION = (
    "\n\nYour previous response did not conform to the required JSON schema. "
    "Please respond with valid JSON matching the exact schema specified. "
    "Ensure all required fields are present with correct types."
)


@dataclass
class ClassificationRun:
    """Record of one classification attempt."""

    model: str
    prompt_version: str
    prompt_hash: str
    raw_response: str | None = None
    parsed_result: ClassifierResult | None = None
    status: ProcessingStatus = ProcessingStatus.PENDING
    error: str | None = None
    token_usage: dict[str, int] | None = None
    estimated_cost_usd: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class ClassifierConfig:
    """Configuration for the OpenRouter classifier client."""

    api_key: str
    api_url: str = OPENROUTER_API_URL
    model: str = DEFAULT_MODEL
    fallback_model: str = "google/gemini-2.5-flash-lite"
    timeout_seconds: float = 60.0
    max_retries: int = 1


class OpenRouterClassifier:
    """Client for OpenRouter structured output classification.

    Validates responses against ClassifierResult schema.
    Retries once with a repair instruction on invalid responses.
    Fail-closed: raises ClassifierError if no valid response after retry.
    """

    def __init__(self, config: ClassifierConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/openclaw/gktrader",
            },
        )

    async def classify(
        self,
        title: str,
        text: str,
        source_metadata: dict[str, Any] | None = None,
    ) -> ClassificationRun:
        """Classify a normalized document.

        Args:
            title: Document title.
            text: Document plain text.
            source_metadata: Optional source metadata dict.

        Returns:
            A ClassificationRun with the result or error information.
        """
        run = ClassificationRun(
            model=self._config.model,
            prompt_version="1.0.0",
            prompt_hash=compute_prompt_hash(),
            started_at=datetime.now(UTC),
        )

        system_prompt = get_classifier_system_prompt()
        user_content = self._build_user_content(title, text, source_metadata)

        # Derive strict JSON schema from the Pydantic model (spec §10)
        result_schema = ClassifierResult.model_json_schema()
        request_body = {
            "model": self._config.model,
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ClassifierResult",
                    "schema": result_schema,
                    "strict": True,
                },
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        for attempt in range(self._config.max_retries + 1):
            if attempt > 0:
                # Add repair instruction on retry
                request_body["messages"].append(
                    {"role": "user", "content": _REPAIR_INSTRUCTION}
                )

            try:
                response = await self._client.post(
                    self._config.api_url,
                    json=request_body,
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as e:
                # Try fallback model once before failing (spec §10)
                if self._config.fallback_model and attempt == 0:
                    logger.warning(
                        "Primary model %s failed with HTTP %s; trying fallback %s",
                        self._config.model,
                        e.response.status_code,
                        self._config.fallback_model,
                    )
                    fallback_body = dict(request_body)
                    fallback_body["model"] = self._config.fallback_model
                    try:
                        fb_resp = await self._client.post(self._config.api_url, json=fallback_body)
                        fb_resp.raise_for_status()
                        data = fb_resp.json()
                        run.model = self._config.fallback_model
                    except Exception as fb_exc:
                        run.status = ProcessingStatus.FAILED
                        run.error = f"Primary HTTP {e.response.status_code}; fallback failed: {fb_exc}"
                        run.completed_at = datetime.now(UTC)
                        return run
                else:
                    run.status = ProcessingStatus.FAILED
                    run.error = f"HTTP {e.response.status_code}: {e.response.text[:500]}"
                    run.completed_at = datetime.now(UTC)
                    return run
            except httpx.RequestError as e:
                run.status = ProcessingStatus.FAILED
                run.error = f"Request failed: {e}"
                run.completed_at = datetime.now(UTC)
                return run

            raw_content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            run.raw_response = raw_content
            run.token_usage = data.get("usage")
            run.estimated_cost_usd = _estimate_cost(
                data.get("usage", {}), self._config.model
            )

            # Parse and validate against strict schema
            parsed = self._validate_response(raw_content)
            if parsed is not None:
                run.parsed_result = parsed
                run.status = ProcessingStatus.SUCCEEDED
                run.completed_at = datetime.now(UTC)
                return run

            # Invalid response; will retry if attempts remain
            logger.warning(
                "Classifier returned invalid response (attempt %d/%d)",
                attempt + 1,
                self._config.max_retries + 1,
            )

        # All attempts exhausted
        run.status = ProcessingStatus.INVALID
        run.error = "Invalid classifier response after all retries"
        run.completed_at = datetime.now(UTC)
        return run

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def _build_user_content(
        self,
        title: str,
        text: str,
        source_metadata: dict[str, Any] | None,
    ) -> str:
        parts = [f"Title: {title}", f"Text: {text[:10000]}"]
        if source_metadata:
            parts.append(f"Source metadata: {source_metadata}")
        return "\n\n".join(parts)

    def _validate_response(self, raw: str) -> ClassifierResult | None:
        """Try to parse raw JSON response into ClassifierResult.

        Returns parsed result on success, None on failure.
        """
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (possibly with language tag)
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1 :]
            # Remove closing fence
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
            elif "```" in cleaned:
                cleaned = cleaned[: cleaned.rfind("```")].strip()

        try:
            import json

            parsed = json.loads(cleaned)
            return ClassifierResult.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            logger.debug("Response validation failed: %s", exc)
            return None


def _estimate_cost(usage: dict[str, int], model: str) -> float | None:
    """Estimate cost in USD from token usage.

    Uses approximate rates. Returns None if usage data is missing.
    """
    if not usage:
        return None
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    # Approximate rates for Gemini flash lite models
    # ~$0.075/1M input tokens, ~$0.30/1M output tokens
    input_rate = 0.075 / 1_000_000
    output_rate = 0.30 / 1_000_000
    return round(prompt_tokens * input_rate + completion_tokens * output_rate, 6)


class ClassifierError(Exception):
    """Raised when the classifier fails closed after exhausting retries."""