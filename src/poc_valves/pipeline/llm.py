from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class LLMExtractionError(RuntimeError):
    """Error raised when LLM extraction fails."""
    pass


class ExtractionClient(ABC):
    """Abstract base class for LLM extraction clients."""

    @abstractmethod
    def extract(
        self, prompt: str, *, images: list[str] | None = None
    ) -> str:
        """Extract data from a prompt."""
        raise NotImplementedError


@dataclass(slots=True)
class MockExtractionClient(ExtractionClient):
    """Mock extraction client for testing."""

    payload: dict[str, Any] | None = None

    def extract(
        self, prompt: str, *, images: list[str] | None = None
    ) -> str:
        """Return mock extraction data."""
        if self.payload is not None:
            return json.dumps(self.payload)
        return json.dumps({"subcategory": "mock", "fields": []})


def parse_json_response(raw: str) -> dict[str, Any]:
    """Parse a JSON response from the LLM, handling markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMExtractionError(
            "Model response was not valid JSON"
        ) from exc