from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .models import LlmChunkResult, PageText


DEFAULT_MODEL = "gpt-4.1"


@dataclass(slots=True)
class OpenAIConfig:
    """Configuration for OpenAI API calls."""

    api_key: str
    model: str = DEFAULT_MODEL
    max_output_tokens: int = 4096
    temperature: float = 0.0
    timeout_seconds: int = 120

    @classmethod
    def from_env(cls) -> "OpenAIConfig":
        """Create config from environment variables."""
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required. Export it in your "
                "shell before running."
            )
        model = os.environ.get(
            "OPENAI_MODEL", DEFAULT_MODEL
        )
        max_output_tokens = int(
            os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "4096")
        )
        temperature = float(
            os.environ.get("OPENAI_TEMPERATURE", "0")
        )
        timeout_seconds = int(
            os.environ.get("OPENAI_TIMEOUT_SECONDS", "120")
        )
        return cls(
            api_key=api_key,
            model=model,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )


def chunk_pages(
    page_texts: list[PageText], pages_per_chunk: int
) -> list[list[PageText]]:
    """Split page texts into chunks of specified size."""
    if pages_per_chunk <= 0:
        raise ValueError(
            "pages_per_chunk must be greater than zero"
        )
    return [
        page_texts[i : i + pages_per_chunk]
        for i in range(0, len(page_texts), pages_per_chunk)
    ]


def build_system_prompt() -> str:
    """Build the system prompt for LLM extraction."""
    return (
        "You are reconstructing a tabular Field Mapping "
        "Register from PDF text. "
        "Return only valid JSON. No markdown fences, no "
        "commentary, no preamble. "
        "Each extracted row must be an object matching the "
        "requested schema. "
        "If a field is not present, use null. Do not invent "
        "values."
    )


def build_user_prompt(chunk_pages: list[PageText]) -> str:
    """Build the user prompt for a chunk of pages."""
    page_blocks = []
    for page in chunk_pages:
        page_blocks.append(f"<<<PAGE {page.page_number}>>>")
        page_blocks.append(page.text.strip())
    return "\n".join(
        [
            "Extract all table rows visible in the "
            "following PDF text chunk.",
            "The source is a Field Mapping Register for "
            "control valve datasheets.",
            "Each row should include these columns:",
            "- serial_number",
            "- enquiry_field_name",
            "- enquiry_field_value",
            "- mapping_logic",
            "- sv2_field_value",
            "- units",
            "- category",
            "- requirement",
            "- similar_field_sv2_field",
            "- similar_field_dependency",
            "- sv2_spec_dependency_terminologies",
            "- sv2_spec_values",
            "- row_number",
            "- field_name",
            "- validations",
            "- notes_and_additional_information",
            "",
            "The source table contains 3 tags. If values "
            "are pipe-separated, keep them as pipe-separated "
            "strings in the output.",
            "Return JSON as an object with keys:",
            '  "rows": [ ... ]',
            '  "warnings": [ ... ]',
            "",
            "Chunk text:",
            *page_blocks,
        ]
    )


def _extract_json_text(raw: str) -> str:
    """Extract JSON text, removing markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _parse_rows_payload(raw: str) -> dict[str, Any]:
    """Parse the rows payload from LLM response."""
    payload = json.loads(_extract_json_text(raw))
    if isinstance(payload, list):
        return {"rows": payload, "warnings": []}
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object or array")
    rows = payload.get("rows", payload.get("data", []))
    warnings = payload.get("warnings", [])
    if not isinstance(rows, list):
        raise ValueError("rows must be a JSON array")
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    return {"rows": rows, "warnings": warnings}


def _build_rows_schema() -> dict[str, Any]:
    """Build the JSON schema for row extraction."""
    row_properties: dict[str, Any] = {
        "serial_number": {"type": ["string", "null"]},
        "enquiry_field_name": {"type": ["string", "null"]},
        "enquiry_field_value": {"type": ["string", "null"]},
        "mapping_logic": {"type": ["string", "null"]},
        "sv2_field_value": {"type": ["string", "null"]},
        "units": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "requirement": {"type": ["string", "null"]},
        "similar_field_sv2_field": {
            "type": ["string", "null"]
        },
        "similar_field_dependency": {
            "type": ["string", "null"]
        },
        "sv2_spec_dependency_terminologies": {
            "type": ["string", "null"]
        },
        "sv2_spec_values": {"type": ["string", "null"]},
        "row_number": {"type": ["string", "null"]},
        "field_name": {"type": ["string", "null"]},
        "validations": {"type": ["string", "null"]},
        "notes_and_additional_information": {
            "type": ["string", "null"]
        },
    }
    return {
        "name": "field_mapping_register_rows",
        "description": (
            "Structured rows extracted from a field mapping "
            "register"
        ),
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": row_properties,
                        "required": ["serial_number"],
                    },
                },
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["rows", "warnings"],
        },
    }


def _messages_request(
    config: OpenAIConfig, user_prompt: str
) -> dict[str, Any]:
    """Build the messages request for OpenAI API."""
    return {
        "model": config.model,
        "input": [
            {
                "role": "system",
                "content": build_system_prompt(),
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "json_schema": _build_rows_schema(),
            }
        },
    }


def _call_openai(
    config: OpenAIConfig, user_prompt: str
) -> str:
    """Call the OpenAI API with the given prompt."""
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "openai is required. Install the 'openai' extra "
            "or run `uv sync --extra openai`."
        ) from exc

    client = OpenAI(
        api_key=config.api_key,
        timeout=config.timeout_seconds,
    )
    response = client.responses.create(
        **_messages_request(config, user_prompt)
    )
    output_text = getattr(response, "output_text", None)
    if not output_text:
        raise RuntimeError(
            "OpenAI response did not include output_text"
        )
    return str(output_text).strip()


def _repair_json(
    config: OpenAIConfig,
    raw_response: str,
    parse_error: str,
) -> str:
    """Attempt to repair invalid JSON from LLM response."""
    repair_prompt = "\n".join(
        [
            "The previous response was invalid JSON.",
            f"Parse error: {parse_error}",
            "Previous response:",
            raw_response,
            "",
            "Return only corrected valid JSON with the "
            "same schema.",
        ]
    )
    return _call_openai(config, repair_prompt)


def extract_rows_from_chunk(
    config: OpenAIConfig,
    chunk_pages: list[PageText],
    *,
    max_parse_retries: int = 1,
) -> LlmChunkResult:
    """Extract rows from a chunk of pages using LLM."""
    user_prompt = build_user_prompt(chunk_pages)
    raw_response = _call_openai(config, user_prompt)
    warnings: list[str] = []

    for attempt in range(max_parse_retries + 1):
        try:
            parsed = _parse_rows_payload(raw_response)
            return LlmChunkResult(
                rows=parsed["rows"],
                raw_response=raw_response,
                chunk_label=(
                    f"pages "
                    f"{chunk_pages[0].page_number}-"
                    f"{chunk_pages[-1].page_number}"
                ),
                warnings=[
                    str(item)
                    for item in parsed.get("warnings", [])
                ]
                + warnings,
            )
        except Exception as exc:
            warnings.append(
                f"parse_attempt_{attempt + 1}: {exc}"
            )
            if attempt >= max_parse_retries:
                break
            raw_response = _repair_json(
                config, raw_response, str(exc)
            )

    raise RuntimeError(
        f"Failed to parse OpenAI response after "
        f"{max_parse_retries + 1} attempt(s). "
        f"Last error: {warnings[-1] if warnings else 'unknown'}"
    )