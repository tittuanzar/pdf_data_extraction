"""OpenAI client wrapper for the SV2 extraction pipeline.

Centralizes client construction, model/temperature defaults, and the
usage/cost accounting that Phase 1 (direct-field extraction) and Phase 2
(judgment-field derivation) both need for every structured-output call.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Type, TypeVar

import dotenv
from openai import OpenAI
from pydantic import BaseModel

from ..config import settings

dotenv.load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Thin wrapper around the OpenAI structured-output API.

    Holds model/temperature/cost-rate defaults (sourced from
    ``core.config.settings``, itself already env-var overridable) and
    exposes a single ``chat_parse`` call that both extraction phases use,
    so usage-extraction and cost-logging logic lives in one place instead
    of being duplicated per call site.
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> None:
        self.model = model or settings.model
        self.temperature = (
            settings.temperature if temperature is None else temperature
        )
        self.prompt_cost_per_1k = settings.prompt_cost_per_1k
        self.completion_cost_per_1k = settings.completion_cost_per_1k
        self.usd_to_inr_rate = settings.usd_to_inr_rate
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        """Lazily construct and cache the OpenAI client."""
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY") or os.getenv(
                "OPENAI_ADMIN_KEY"
            )
            if not api_key:
                raise RuntimeError("Missing OpenAI API key in environment")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def format_cost(self, cost_usd: float) -> str:
        """Format a USD cost for logging, with its Rs equivalent alongside
        it using the configured static ``usd_to_inr_rate``."""
        if not cost_usd:
            return "(not configured)"
        if self.usd_to_inr_rate:
            return f"${cost_usd:.6f} (₹{cost_usd * self.usd_to_inr_rate:.4f})"
        return f"${cost_usd:.6f}"

    def _usage_dict(self, response: Any) -> dict[str, int]:
        """Extract a normalized usage dict from a chat-completions response,
        tolerating both dict-shaped and attribute-shaped ``usage`` objects.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }

        def _get(key: str) -> int:
            raw = (
                usage.get(key, 0)
                if isinstance(usage, dict)
                else getattr(usage, key, 0)
            )
            return int(raw or 0)

        prompt_tokens = _get("prompt_tokens")
        completion_tokens = _get("completion_tokens")
        total_tokens = _get("total_tokens") or (
            prompt_tokens + completion_tokens
        )
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _cost(self, usage: dict[str, int]) -> float:
        if not (self.prompt_cost_per_1k or self.completion_cost_per_1k):
            return 0.0
        return (
            usage["prompt_tokens"] / 1000.0 * self.prompt_cost_per_1k
            + usage["completion_tokens"]
            / 1000.0
            * self.completion_cost_per_1k
        )

    def chat_parse(
        self,
        *,
        system_prompt: str,
        user_content: Any,
        response_format: Type[T],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        log_label: str = "LLM call",
    ) -> tuple[T, dict[str, int], float]:
        """Run a structured-output chat completion and return the parsed
        result alongside its usage dict and cost.

        Raises ``RuntimeError`` if the API returns no choices or the
        response could not be parsed into ``response_format``.
        """
        response = self.client.chat.completions.parse(
            model=model or self.model,
            temperature=(
                self.temperature if temperature is None else temperature
            ),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=response_format,
        )

        usage = self._usage_dict(response)
        cost = self._cost(usage)
        logger.info(
            "%s usage: prompt=%s completion=%s total=%s cost=%s",
            log_label,
            usage["prompt_tokens"],
            usage["completion_tokens"],
            usage["total_tokens"],
            self.format_cost(cost),
        )

        if not getattr(response, "choices", None):
            raise RuntimeError(f"{log_label}: LLM returned no choices")

        message = response.choices[0].message
        if not hasattr(message, "parsed"):
            raise RuntimeError(f"{log_label}: LLM response could not be parsed")

        result = message.parsed
        if isinstance(result, dict):
            result = response_format.model_validate(result)
        if not isinstance(result, response_format):
            raise RuntimeError(
                f"{log_label}: LLM did not return a {response_format.__name__} instance"
            )

        return result, usage, cost
