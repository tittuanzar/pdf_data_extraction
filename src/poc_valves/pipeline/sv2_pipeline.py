from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import dotenv
import pandas as pd
from openai import OpenAI
from pydantic import BaseModel, Field

from ..config import settings
from ..pydantic_output import ParsedOutput
from ..schema.output_model import (
    _sanitize_field_name,
    build_output_model,
)
from .postprocess import postprocess_tags

dotenv.load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
DEFAULT_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "1.0"))

# Optional cost configuration (per 1000 tokens).
# Set via env to compute cost; defaults to 0 (disabled).
PROMPT_COST_PER_1K = float(os.getenv("OPENAI_PROMPT_COST_PER_1K", "0"))
COMPLETION_COST_PER_1K = float(
    os.getenv("OPENAI_COMPLETION_COST_PER_1K", "0")
)


@dataclass
class FieldSpecLite:
    """Lightweight field specification for extraction."""

    sv2_field_name: str
    source_hint: str
    logic_type: Optional[str] = None


def load_field_registry(xlsx_path: str) -> list[FieldSpecLite]:
    """Load field registry from an Excel file."""
    df = pd.read_excel(xlsx_path, sheet_name="Sheet1")
    df.columns = [" ".join(str(c).split()) for c in df.columns]
    registry: list[FieldSpecLite] = []
    for _, row in df.iterrows():
        registry.append(
            FieldSpecLite(
                sv2_field_name=str(
                    row.get("Sv2 Field Name") or ""
                ).strip(),
                source_hint=str(
                    row.get("Enquiry Field Name (Customer Spec)")
                    or ""
                ).strip(),
                logic_type=str(
                    row.get("Mapping Category") or ""
                ).strip(),
            )
        )
    return registry


def _get_client():
    """Get an OpenAI client instance."""
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv(
        "OPENAI_ADMIN_KEY"
    )
    if not api_key:
        raise RuntimeError("Missing OpenAI API key in environment")
    return OpenAI(api_key=api_key)


class ExtractedField(BaseModel):
    """A single extracted field from a page."""

    field_name: str
    raw_value: Optional[str]
    source_quote: Optional[str]
    page_number: Optional[int]
    confidence: float


class PageExtraction(BaseModel):
    """Extraction result for a single page."""

    tag_number: Optional[str]
    fields: list[ExtractedField]


def extract_direct_fields_with_context(
    registry: list[FieldSpecLite],
    page_image_b64: str,
    page_text: str,
    page_number: int,
    guide: Optional[str] = None,
    tables: Optional[list[dict]] = None,
    debug: bool = False,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> PageExtraction | tuple[PageExtraction, object]:
    """Extract direct fields from a page with context."""
    direct_fields = [
        f
        for f in registry
        if (not f.logic_type)
        or f.logic_type.lower() == "direct"
    ]
    field_list = "\n".join(
        f"- {f.sv2_field_name}: look for '{f.source_hint}'"
        for f in direct_fields
    )

    user_parts = []
    user_parts.append(
        {
            "type": "text",
            "text": (
                f"Extract these fields:\n{field_list}\n\n"
                f"OCR text for this page:\n{page_text}"
            ),
        }
    )
    if tables:
        tables_text = "\n\n".join(
            f"Table {i+1}: {json.dumps(t)}"
            for i, t in enumerate(tables)
        )
        user_parts.append(
            {
                "type": "text",
                "text": f"Detected tables on this page:\n{tables_text}",
            }
        )
    # only include an image part if we have valid base64 content
    if page_image_b64 and str(page_image_b64).strip():
        try:
            # validate base64
            base64.b64decode(page_image_b64, validate=True)
            user_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{page_image_b64}"
                    },
                }
            )
        except Exception:
            logger.warning(
                "Provided image_b64 for page %s is not valid "
                "base64; skipping image part",
                page_number,
            )

    if guide:
        user_parts.insert(
            0,
            {
                "type": "text",
                "text": f"Guide for extraction:\n{guide}",
            },
        )

    client = _get_client()

    try:
        response = client.chat.completions.parse(
            model=model or DEFAULT_MODEL,
            temperature=(
                temperature
                if temperature is not None
                else DEFAULT_TEMPERATURE
            ),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract field values verbatim from an "
                        "engineering datasheet page. "
                        "Only report a value if you can point to "
                        "where it appears — never infer or calculate. "
                        "If a field is not present on this page, set "
                        "raw_value to null. "
                        "Include a short quote as evidence and the "
                        "field's confidence."
                    ),
                },
                {"role": "user", "content": user_parts},
            ],
            response_format=PageExtraction,
        )
    except Exception as exc:
        logger.exception(
            "LLM extraction failed for page %s", page_number
        )
        raise

    # Capture usage info if present
    usage = None
    try:
        usage = getattr(response, "usage", None) or (
            getattr(response, "__dict__", {}).get("usage")
            if hasattr(response, "__dict__")
            else None
        )
    except Exception:
        usage = None

    if usage:
        prompt_tokens = int(
            usage.get("prompt_tokens", 0)
            if isinstance(usage, dict)
            else int(getattr(usage, "prompt_tokens", 0) or 0)
        )
        completion_tokens = int(
            usage.get("completion_tokens", 0)
            if isinstance(usage, dict)
            else int(getattr(usage, "completion_tokens", 0) or 0)
        )
        total_tokens = int(
            usage.get(
                "total_tokens", prompt_tokens + completion_tokens
            )
            if isinstance(usage, dict)
            else int(
                getattr(
                    usage,
                    "total_tokens",
                    prompt_tokens + completion_tokens,
                )
                or (prompt_tokens + completion_tokens)
            )
        )
        usage_dict = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
    else:
        prompt_tokens = completion_tokens = total_tokens = 0
        usage_dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    # compute cost if rates configured
    cost = 0.0
    if PROMPT_COST_PER_1K or COMPLETION_COST_PER_1K:
        cost = (
            (prompt_tokens / 1000.0) * PROMPT_COST_PER_1K
            + (completion_tokens / 1000.0) * COMPLETION_COST_PER_1K
        )
    logger.info(
        "LLM usage page %s: prompt=%s completion=%s total=%s "
        "cost=%s",
        page_number,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        f"${cost:.6f}" if cost else "(not configured)",
    )

    if not getattr(response, "choices", None):
        logger.error(
            "LLM returned no choices for page %s: %r",
            page_number,
            getattr(response, "__dict__", repr(response)),
        )
        raise RuntimeError("LLM returned no choices")

    if not hasattr(response.choices[0].message, "parsed"):
        logger.error(
            "LLM response missing parsed attribute for page %s; "
            "raw response: %r",
            page_number,
            getattr(response, "__dict__", repr(response)),
        )
        raise RuntimeError("LLM response could not be parsed")

    result = response.choices[0].message.parsed
    for f in result.fields:
        f.page_number = page_number

    if debug:
        return result, response, usage_dict, cost
    return result


def extract_sv2_output_from_pages(
    pages: list[dict],
    guide: Optional[str] = None,
    debug: bool = False,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    use_engineered_defaults: Optional[bool] = None,
) -> ParsedOutput:
    """Ask the LLM to return the final SV2 datasheet payload."""
    client = _get_client()
    page_payload = [
        {
            "page_number": int(p.get("page_number", 0) or 0),
            "text": str(p.get("text", "") or ""),
            "tables": p.get("tables") or [],
        }
        for p in pages
    ]

    response = client.chat.completions.parse(
        model=model or DEFAULT_MODEL,
        temperature=(
            temperature
            if temperature is not None
            else DEFAULT_TEMPERATURE
        ),
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract a valve datasheet into the exact "
                    "ParsedOutput schema. "
                    "Return a top-level object with a 'tags' array. "
                    "Each tag is one SV2ValveDatasheet. "
                    "Use the field names exactly as defined in the "
                    "schema. For optional fields, use null when "
                    "unknown. "
                    "Do not output markdown or any text outside "
                    "the JSON object."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Guide for extraction:\n{guide}\n\n"
                            "Pages:\n"
                            f"{json.dumps(page_payload, ensure_ascii=False)}"
                        ),
                    }
                ],
            },
        ],
        response_format=ParsedOutput,
    )

    if not getattr(response, "choices", None):
        raise RuntimeError(
            "LLM returned no choices for ParsedOutput"
        )

    parsed = response.choices[0].message.parsed
    if isinstance(parsed, dict):
        parsed = ParsedOutput.model_validate(parsed)
    if not isinstance(parsed, ParsedOutput):
        raise RuntimeError(
            "LLM did not return a ParsedOutput instance"
        )

    if debug:
        try:
            parsed.model_extra = {
                "raw_llm": getattr(
                    response.choices[0].message, "content", None
                )
            }
        except Exception:
            pass

    # --- Postprocessing: fill engineered defaults & derived fields ---
    if use_engineered_defaults is None:
        use_engineered_defaults = settings.use_engineered_defaults
    parsed.tags = postprocess_tags(
        parsed.tags,
        use_engineered_defaults=use_engineered_defaults,
    )

    return parsed


def _serialize_resp(r):
    """Serialize an LLM response object for debug output."""
    try:
        if r is None:
            return None
        if hasattr(r, "to_dict"):
            return r.to_dict()
        if hasattr(r, "choices"):
            choices_out = []
            for c in getattr(r, "choices", []):
                msg = getattr(c, "message", None)
                if msg is not None:
                    content = getattr(msg, "content", None)
                    parsed = getattr(msg, "parsed", None)
                    choices_out.append(
                        {
                            "content": (
                                content
                                if isinstance(
                                    content,
                                    (
                                        str,
                                        dict,
                                        list,
                                        int,
                                        float,
                                        type(None),
                                    ),
                                )
                                else str(content)
                            ),
                            "parsed": (
                                parsed
                                if isinstance(
                                    parsed,
                                    (
                                        str,
                                        dict,
                                        list,
                                        int,
                                        float,
                                        type(None),
                                    ),
                                )
                                else str(parsed)
                            ),
                        }
                    )
                else:
                    choices_out.append(str(c))
            return {"choices": choices_out}
        return str(r)
    except Exception:
        return repr(r)


def run_pipeline_sv2(
    registry: list[FieldSpecLite],
    pages: list[dict],
    guide: Optional[str] = None,
    debug: bool = False,
) -> dict:
    """Run the SV2 extraction pipeline."""
    per_page_results = []
    # organize results by tag_number
    tags_map: dict[str, dict] = {}
    # precompute all field names from registry
    registry_field_names = [
        f.sv2_field_name for f in registry
    ]
    # build a dynamic Pydantic model from the reference guide
    try:
        rg_path = str(
            Path(__file__).resolve().parents[3]
            / "reference_guide.xlsx"
        )
        OutputModel, field_mapping = build_output_model(rg_path)
    except Exception:
        OutputModel = None
        field_mapping = {
            name: _sanitize_field_name(name)
            for name in registry_field_names
        }

    for p in pages:
        page_num = int(p.get("page_number", 0))
        logger.info("Processing page %s", page_num)
        page_text = p.get("text", "")
        image_b64 = p.get("image_b64", "")
        tables = p.get("tables")

        extracted = extract_direct_fields_with_context(
            registry,
            image_b64,
            page_text,
            page_num,
            guide=guide,
            tables=tables,
            debug=debug,
        )
        raw_resp = None
        usage_dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        cost = 0.0
        if debug and isinstance(extracted, tuple):
            if len(extracted) == 4:
                (
                    page_extraction,
                    raw_resp,
                    usage_dict,
                    cost,
                ) = extracted
            else:
                page_extraction, raw_resp = extracted
        else:
            page_extraction = extracted

        page_dict = page_extraction.dict()
        if debug:
            page_dict["raw_llm"] = _serialize_resp(raw_resp)
            page_dict["usage"] = usage_dict
            page_dict["cost"] = round(cost, 8)

        per_page_results.append(page_dict)

        # determine tag for this page
        tag_key = str(
            getattr(page_extraction, "tag_number", None)
            or p.get("tag_number")
            or "unknown"
        )
        if tag_key not in tags_map:
            tags_map[tag_key] = {
                "tag_number": tag_key,
                "pages": [],
                "fields": {
                    name: {
                        "raw_value": None,
                        "source": None,
                        "page_number": None,
                        "confidence": 0.0,
                        "source_quote": None,
                    }
                    for name in registry_field_names
                },
            }

        tags_map[tag_key]["pages"].append(page_num)

        for ef in page_extraction.fields:
            name = ef.field_name
            if name not in tags_map[tag_key]["fields"]:
                tags_map[tag_key]["fields"][name] = {
                    "raw_value": ef.raw_value,
                    "source": "direct",
                    "page_number": ef.page_number,
                    "confidence": ef.confidence,
                    "source_quote": ef.source_quote,
                }
            else:
                if ef.raw_value is not None:
                    tags_map[tag_key]["fields"][name] = {
                        "raw_value": ef.raw_value,
                        "source": "direct",
                        "page_number": ef.page_number,
                        "confidence": ef.confidence,
                        "source_quote": ef.source_quote,
                    }

    result = {
        "pages": per_page_results,
        "tags": list(tags_map.values()),
    }
    # aggregate totals and log
    try:
        total_prompt = sum(
            p.get("usage", {}).get("prompt_tokens", 0)
            for p in per_page_results
        )
        total_completion = sum(
            p.get("usage", {}).get("completion_tokens", 0)
            for p in per_page_results
        )
        total_tokens = sum(
            p.get("usage", {}).get("total_tokens", 0)
            for p in per_page_results
        )
        total_cost = sum(
            p.get("cost", 0.0) for p in per_page_results
        )
        logger.info(
            "Aggregate LLM usage for run: prompt=%s "
            "completion=%s total=%s cost=%s",
            total_prompt,
            total_completion,
            total_tokens,
            f"${total_cost:.6f}" if total_cost else "(not configured)",
        )
        result["usage"] = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
        }
        result["cost"] = round(total_cost, 8)
    except Exception:
        pass

    return result