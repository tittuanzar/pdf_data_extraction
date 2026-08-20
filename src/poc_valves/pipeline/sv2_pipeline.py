from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import dotenv
import pandas as pd
from openai import OpenAI
from pydantic import BaseModel, Field

from ..config import get_prompt, load_reference_fields, settings
from ..pydantic_output import (
    ParsedOutput,
    PartialSV2ValveDatasheet,
    SV2ValveDatasheet,
)
from ..schema.output_model import (
    _sanitize_field_name,
    build_output_model,
)
from . import postprocess

dotenv.load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Sourced from config/settings.yml's `llm` block (config.py already
# applies OPENAI_MODEL / OPENAI_TEMPERATURE / OPENAI_PROMPT_COST_PER_1K /
# OPENAI_COMPLETION_COST_PER_1K / OPENAI_USD_TO_INR_RATE env-var overrides
# on top of the YAML values, so those env vars still work as before).
DEFAULT_MODEL = settings.model
DEFAULT_TEMPERATURE = settings.temperature

# Optional cost configuration (per 1000 tokens). Defaults to 0 (disabled)
# if unset in settings.yml/env.
PROMPT_COST_PER_1K = settings.prompt_cost_per_1k
COMPLETION_COST_PER_1K = settings.completion_cost_per_1k

# Static USD -> INR rate used only to display a Rs figure alongside cost
# in logs (not a live exchange rate — see settings.yml to update it).
USD_TO_INR_RATE = settings.usd_to_inr_rate


def _format_cost(cost_usd: float) -> str:
    """Format a USD cost for logging, with its Rs equivalent alongside it
    using the configured static USD_TO_INR_RATE."""
    if not cost_usd:
        return "(not configured)"
    if USD_TO_INR_RATE:
        return f"${cost_usd:.6f} (₹{cost_usd * USD_TO_INR_RATE:.4f})"
    return f"${cost_usd:.6f}"


@dataclass
class FieldSpecLite:
    """Lightweight field specification for extraction."""

    sv2_field_name: str
    source_hint: str
    logic_type: Optional[str] = None


# Raw enquiry values that feed Phase-2 derived-field logic but are never
# exposed directly in the final 66-field output. Most of reference.yml's 27
# "derived" fields key off enquiry rows outside the 34 "direct" SV2 fields
# (e.g. Area Classification, Power Failure Position, Positioner MFR/Model)
# — Phase 1 captures these alongside the direct fields (same page, same LLM
# call) so Phase 2 never has to re-read raw OCR text.
AUXILIARY_SOURCE_FIELDS: list[FieldSpecLite] = [
    FieldSpecLite("raw_upstream_condition", "Upstream Condition (Row 15)"),
    FieldSpecLite(
        "raw_mdmt_process_notes", "MDMT / Process Notes (DS Page 2)"
    ),
    FieldSpecLite(
        "raw_inlet_density_or_mw",
        "Inlet Density / Specific Gravity / Molecular Mass (Row 22)",
    ),
    FieldSpecLite(
        "raw_characteristic", "Rated Cv / Characteristic (Row 34)"
    ),
    FieldSpecLite(
        "raw_tightness_requirements", "Tightness Requirements (Row 7)"
    ),
    FieldSpecLite(
        "raw_power_failure_position", "Power Failure Position (Row 9)"
    ),
    FieldSpecLite(
        "raw_air_failure_valve_action", "Air Failure Valve (Row 52)"
    ),
    FieldSpecLite(
        "raw_available_air_supply_pressure",
        "Available Air Supply Pressure (Row 8)",
    ),
    FieldSpecLite("raw_positioner_mfr", "Positioner MFR (Row 56)"),
    FieldSpecLite("raw_positioner_model", "Positioner Model (Row 56)"),
    FieldSpecLite("raw_signal_inlet", "Signal Inlet (Row 57)"),
    FieldSpecLite(
        "raw_increase_signal_direction", "Increase Signal Valve (Row 58)"
    ),
    FieldSpecLite(
        "raw_area_classification", "Area Classification (Row 4)"
    ),
    FieldSpecLite(
        "raw_position_transmitter_yn", "Position Transmitter (Row 61)"
    ),
    FieldSpecLite(
        "raw_position_transmitter_voltage",
        "Position Transmitter Voltage (Row 62)",
    ),
    FieldSpecLite("raw_airset_mfr_model", "Air Set MFR / Model (Row 72)"),
]

# The 11 "derived" fields (of 27) whose logic requires judgment/vendor-
# catalog knowledge rather than a deterministic formula — handled by one
# focused LLM call per tag in Phase 2 (see derive_judgment_fields_llm).
# Maps SV2ValveDatasheet attribute name -> reference.yml sv2_field_name
# (used to pull each field's derivation logic text for the prompt).
JUDGMENT_FIELD_ATTR_TO_NAME: dict[str, str] = {
    "design_temp_min_mdmt": "Design Temp (MIN) / MDMT",
    "flow_characteristic": "Flow Characteristic",
    "supply_pressure": "Supply Pressure",
    "positioner_make": "Positioner Make",
    "positioner_model": "Positioner Model",
    "positioner_protocol_casing": "Positioner Protocol / Casing",
    "positioner_action_input_signal": "Positioner Action / Input Signal",
    "positioner_certification": "Positioner Certification",
    "position_transmitter": "Position Transmitter",
    "airset_make_model_qty": "Airset Make/Model/Qty",
    "ibr_applicability": "IBR Applicability",
}


def load_field_registry(_xlsx_path: str | None = None) -> list[FieldSpecLite]:
    """Load field registry from reference.yml."""
    fields = load_reference_fields()
    registry: list[FieldSpecLite] = []
    for entry in fields:
        source = entry.get("source", {}) if isinstance(entry.get("source"), dict) else {}
        registry.append(
            FieldSpecLite(
                sv2_field_name=str(entry.get("sv2_field_name") or "").strip(),
                source_hint=str(source.get("enquiry_field_name") or "").strip(),
                logic_type=str(entry.get("extraction_type") or "").strip(),
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


class DerivedJudgmentFields(BaseModel):
    """Phase-2 (LLM half) structured output.

    The 11 "derived" reference.yml fields (of 27) whose logic can't be
    reduced to a deterministic formula — vendor-catalog selection, compound
    conditional reasoning, or free-text judgment calls. See
    JUDGMENT_FIELD_ATTR_TO_NAME for the reference.yml mapping.
    """

    design_temp_min_mdmt: Optional[str] = None
    flow_characteristic: Optional[str] = None
    supply_pressure: Optional[str] = None
    positioner_make: Optional[str] = None
    positioner_model: Optional[str] = None
    positioner_protocol_casing: Optional[str] = None
    positioner_action_input_signal: Optional[str] = None
    positioner_certification: Optional[str] = None
    position_transmitter: Optional[str] = None
    airset_make_model_qty: Optional[str] = None
    ibr_applicability: Optional[bool] = None


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
    """Extract direct fields from a page with context.

    Also extracts AUXILIARY_SOURCE_FIELDS alongside the direct fields —
    raw enquiry values that feed Phase-2 derived-field logic but are not
    part of the 66-field output — so Phase 2 never needs a second OCR
    read of the raw page text/tables.
    """
    direct_fields = [
        f
        for f in registry
        if (not f.logic_type)
        or f.logic_type.lower() == "direct"
    ] + AUXILIARY_SOURCE_FIELDS
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
                    "content": get_prompt("data_extraction.system_prompt"),
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
        _format_cost(cost),
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
                "content": get_prompt("tags_extraction.system_prompt"),
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
    parsed.tags = postprocess.postprocess_tags(
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


def _build_sv2_field_name_to_attr_map() -> dict[str, str]:
    """Map each reference.yml ``sv2_field_name`` to its SV2ValveDatasheet
    attribute name.

    Both reference.yml (ordered by ``id``, 1-66) and SV2ValveDatasheet's
    field declaration order (serial 1 Tag No ... serial 66 PESO
    Certificate) follow the same serial numbering, so a position-based zip
    is reliable and avoids fragile name-sanitization heuristics.
    """
    fields = load_reference_fields()
    attrs = list(SV2ValveDatasheet.model_fields.keys())
    if len(fields) != len(attrs):
        logger.warning(
            "reference.yml field count (%d) != SV2ValveDatasheet field "
            "count (%d); sv2_field_name mapping may be incomplete",
            len(fields),
            len(attrs),
        )
    mapping: dict[str, str] = {}
    for entry, attr in zip(fields, attrs):
        name = str(entry.get("sv2_field_name") or "").strip()
        if name:
            mapping[name] = attr
    return mapping


def derive_judgment_fields_llm(
    tag: PartialSV2ValveDatasheet,
    aux: dict[str, Optional[str]],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    debug: bool = False,
) -> DerivedJudgmentFields | tuple:
    """Phase 2 (LLM half): compute the 11 judgment-based derived fields
    from this tag's Phase-1 direct-field values and auxiliary raw source
    values only — no raw OCR text/tables are re-read here.
    """
    client = _get_client()

    direct_fields_json = json.dumps(
        {k: v for k, v in tag.model_dump().items() if v is not None},
        ensure_ascii=False,
    )
    auxiliary_fields_json = json.dumps(
        {k: v for k, v in aux.items() if v}, ensure_ascii=False
    )

    reference_by_name = {
        str(e.get("sv2_field_name") or "").strip(): e
        for e in load_reference_fields()
    }
    field_logic_lines = [
        f"- {sv2_name} ({attr}): "
        f"{str(reference_by_name.get(sv2_name, {}).get('logic') or '').strip()}"
        for attr, sv2_name in JUDGMENT_FIELD_ATTR_TO_NAME.items()
    ]

    user_prompt = get_prompt(
        "derived_field_judgment.user_prompt",
        direct_fields_json=direct_fields_json,
        auxiliary_fields_json=auxiliary_fields_json,
        field_logic_block="\n".join(field_logic_lines),
    )

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
                "content": get_prompt(
                    "derived_field_judgment.system_prompt"
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        response_format=DerivedJudgmentFields,
    )

    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total_tokens = int(
        getattr(usage, "total_tokens", None)
        or (prompt_tokens + completion_tokens)
    )
    cost = 0.0
    if PROMPT_COST_PER_1K or COMPLETION_COST_PER_1K:
        cost = (
            (prompt_tokens / 1000.0) * PROMPT_COST_PER_1K
            + (completion_tokens / 1000.0) * COMPLETION_COST_PER_1K
        )
    logger.info(
        "LLM usage (Phase 2 judgment) tag %s: prompt=%s completion=%s "
        "total=%s cost=%s",
        tag.tag_no,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        _format_cost(cost),
    )

    if not getattr(response, "choices", None):
        raise RuntimeError(
            "LLM returned no choices for DerivedJudgmentFields"
        )

    result = response.choices[0].message.parsed
    if debug:
        return (
            result,
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            cost,
        )
    return result


def _finalize_tag(
    partial: PartialSV2ValveDatasheet,
) -> SV2ValveDatasheet:
    """Validate a fully-processed partial tag into the strict
    SV2ValveDatasheet.

    Any required field still missing after all three phases indicates a
    real extraction/derivation gap for that tag — log a warning and
    coerce it to a visible placeholder rather than failing the whole
    batch: this is a review workbook, so engineers need to see which
    cells need manual entry, not a hard crash.
    """
    data = partial.model_dump()
    missing = [
        name
        for name, field in SV2ValveDatasheet.model_fields.items()
        if field.is_required() and data.get(name) is None
    ]
    if missing:
        logger.warning(
            "Tag %s missing required fields after all phases: %s",
            data.get("tag_no"),
            missing,
        )
        for name in missing:
            data[name] = "NOT EXTRACTED"
    return SV2ValveDatasheet(**data)


# A tag's enquiry data spans a "main" datasheet page — starts with the
# row "1 Tag No. ..." — followed by one or more continuation pages that
# start with "Tag Number: ..." and carry Specification/Process Notes
# (MDMT, IBR applicability, etc. — inputs several derived-field rules
# depend on). Pages matching neither pattern (e.g. a cover/title page)
# carry no tag data at all.
_MAIN_PAGE_RE = re.compile(r"1\s+Tag\s*No\.", re.IGNORECASE)
_CONTINUATION_PAGE_RE = re.compile(r"Tag\s*Number\s*:", re.IGNORECASE)


def _group_pages_by_tag(pages: list[dict]) -> list[list[dict]]:
    """Group PDF pages into per-tag page groups.

    Without this, treating every PDF page as its own tag (the previous
    behaviour) produces a bogus extra sheet per continuation page — mostly
    empty, duplicate tag number — instead of merging it into the main
    page's tag, and it means the process-notes text a continuation page
    carries never reaches the same Phase-1 call as its tag's main page.
    """
    groups: list[list[dict]] = []
    for p in pages:
        text = str(p.get("text", "") or "")
        if _MAIN_PAGE_RE.search(text):
            groups.append([p])
        elif _CONTINUATION_PAGE_RE.search(text):
            if groups:
                groups[-1].append(p)
            else:
                # Continuation page with no preceding main page in this
                # batch — keep it as its own group rather than dropping it.
                groups.append([p])
        # else: no tag markers on this page (e.g. cover page) — skip.
    return groups


def _merge_page_group(group: list[dict]) -> dict:
    """Merge one tag's page group into a single page-shaped dict for
    Phase 1 extraction: concatenated text/tables across all pages in the
    group, image taken from the main (first) page only.
    """
    merged_text = "\n\n".join(
        str(p.get("text", "") or "") for p in group
    )
    merged_tables = [
        t for p in group for t in (p.get("tables") or [])
    ]
    return {
        "page_number": group[0].get("page_number"),
        "text": merged_text,
        "tables": merged_tables,
        "image_b64": group[0].get("image_b64", ""),
    }


# Row 1 of the enquiry DS lays out "Tag No." | "PID" | <value> | <P&ID doc
# ref> in a single visual row, where "PID" is the sub-column header (short
# for the P&ID document the tag is cross-referenced to), not part of the
# tag number itself. In linear OCR/table text this header sits directly
# before the value (e.g. "PID 1803-FV-10401"), so it sometimes leaks into
# the extracted Tag No — strip a leading "PID" token before it becomes the
# tag name and, downstream, the Excel sheet name.
_TAG_NO_PID_PREFIX_RE = re.compile(r"^\s*PID\b[\s:\-]*", re.IGNORECASE)


def _normalize_tag_no(value: Optional[str]) -> Optional[str]:
    """Strip a leading 'PID' column-header token from an extracted tag
    number, if present. See _TAG_NO_PID_PREFIX_RE for why this happens.
    """
    if not value:
        return value
    return _TAG_NO_PID_PREFIX_RE.sub("", value).strip()


# The source enquiry datasheet uses a comma as the decimal separator
# throughout (temperature, pressure, flow, density, viscosity, etc. —
# e.g. "10,5", "6,8", "0,276"), not a period. Downstream numeric parsing
# (postprocess._parse_numeric) and the reference guide's documented output
# format both expect periods, so this is normalized as soon as a value is
# extracted rather than at every call site that later parses it.
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")


def _normalize_decimal_commas(value: Optional[str]) -> Optional[str]:
    """Replace a comma decimal separator between digits (e.g. '10,5') with
    a period ('10.5'). Leaves commas that aren't between two digits alone.
    """
    if not value:
        return value
    return _DECIMAL_COMMA_RE.sub(".", value)


def _accumulate_usage(
    total: dict[str, int], usage: Optional[dict]
) -> None:
    """Add one LLM call's token usage dict into a running total dict."""
    if not usage:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key, 0) or 0)


def run_three_phase_pipeline(
    pages: list[dict],
    guide: Optional[str] = None,
    debug: bool = False,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    use_engineered_defaults: Optional[bool] = None,
) -> ParsedOutput:
    """Extract SV2 tags via three phases.

    Each tag spans a main datasheet page plus one or more continuation
    pages (process notes, MDMT, IBR applicability, etc.) — pages are first
    grouped by tag (``_group_pages_by_tag``) and merged (``_merge_page_group``)
    so Phase 1 sees a tag's full text in a single call.

      1. Direct-field + auxiliary verbatim extraction (LLM, narrow scope,
         no computation) — ``extract_direct_fields_with_context``.
      2. Derived-field computation: deterministic Python rules first
         (``postprocess.apply_pre_llm_derivations``/
         ``apply_post_llm_derivations``), then one focused LLM call per
         tag for the remaining judgment-based fields
         (``derive_judgment_fields_llm``), using Phase 1's output only.
      3. Engineered-field defaults (deterministic, org-standard, no LLM;
         ``postprocess.apply_engineered_defaults``).

    Replaces the single-pass ``extract_sv2_output_from_pages`` as the live
    entry point; that function is kept, unused, for rollback.

    Logs a single aggregate token-usage/cost line for the whole request
    (summed across every tag's Phase-1 and Phase-2 LLM calls) once all
    tags have been processed, in addition to the existing per-call usage
    lines logged inside ``extract_direct_fields_with_context`` and
    ``derive_judgment_fields_llm``.

    ``debug`` is currently reserved (both internal LLM calls always fetch
    usage data to feed the aggregate total above) — raw LLM responses are
    not yet surfaced through this parameter.
    """
    if use_engineered_defaults is None:
        use_engineered_defaults = settings.use_engineered_defaults

    registry = load_field_registry()
    name_to_attr = _build_sv2_field_name_to_attr_map()

    page_groups = _group_pages_by_tag(pages)
    logger.info(
        "Three-phase pipeline: %d page(s) grouped into %d tag(s)",
        len(pages),
        len(page_groups),
    )

    total_usage: dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    total_cost = 0.0

    tags: list[SV2ValveDatasheet] = []
    for group in page_groups:
        p = _merge_page_group(group)
        page_num = int(p.get("page_number", 0) or 0)
        logger.info(
            "Three-phase pipeline: processing tag starting at page %s "
            "(%d page(s) in group)",
            page_num,
            len(group),
        )

        # --- Phase 1: direct + auxiliary verbatim extraction ---
        # Always request the debug tuple internally (regardless of the
        # caller's `debug` flag) so this call's token usage/cost can be
        # folded into the request-level total logged below; the raw LLM
        # response itself is discarded unless the caller asked for debug.
        extraction = extract_direct_fields_with_context(
            registry,
            p.get("image_b64", ""),
            p.get("text", ""),
            page_num,
            guide=guide,
            tables=p.get("tables"),
            debug=True,
        )
        page_extraction, _raw_resp, phase1_usage, phase1_cost = extraction
        _accumulate_usage(total_usage, phase1_usage)
        total_cost += phase1_cost

        direct_values: dict[str, object] = {}
        aux_values: dict[str, Optional[str]] = {}
        for ef in page_extraction.fields:
            if ef.raw_value is None:
                continue
            value = _normalize_decimal_commas(ef.raw_value)
            attr = name_to_attr.get(ef.field_name)
            if attr is not None:
                if attr == "tag_no":
                    value = _normalize_tag_no(value)
                direct_values[attr] = value
            elif ef.field_name.startswith("raw_"):
                aux_values[ef.field_name] = value

        if not direct_values.get("tag_no"):
            direct_values["tag_no"] = _normalize_tag_no(
                page_extraction.tag_number
            ) or f"page-{page_num}"

        partial = PartialSV2ValveDatasheet(**direct_values)

        # --- Phase 2: derived fields ---
        partial = postprocess.apply_pre_llm_derivations(
            partial, aux_values
        )
        try:
            judgment, phase2_usage, phase2_cost = (
                derive_judgment_fields_llm(
                    partial,
                    aux_values,
                    model=model,
                    temperature=temperature,
                    debug=True,
                )
            )
            _accumulate_usage(total_usage, phase2_usage)
            total_cost += phase2_cost
            judgment_updates = {
                k: v
                for k, v in judgment.model_dump().items()
                if v is not None and getattr(partial, k, None) is None
            }
            if judgment_updates:
                partial = partial.model_copy(update=judgment_updates)
        except Exception:
            logger.exception(
                "Phase 2 LLM judgment call failed for tag %s; "
                "continuing with deterministic derivations only",
                direct_values.get("tag_no"),
            )
        partial = postprocess.apply_post_llm_derivations(
            partial, aux_values
        )

        # --- Phase 3: engineered defaults ---
        partial = postprocess.apply_engineered_defaults(
            partial, use_engineered_defaults=use_engineered_defaults
        )

        tags.append(_finalize_tag(partial))

    logger.info(
        "Three-phase pipeline TOTAL usage for this request: %d tag(s) "
        "from %d page(s) — prompt=%s completion=%s total=%s cost=%s",
        len(tags),
        len(pages),
        total_usage["prompt_tokens"],
        total_usage["completion_tokens"],
        total_usage["total_tokens"],
        _format_cost(total_cost),
    )

    return ParsedOutput(tags=tags)


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
        rg_path = str(settings.reference_guide_abs_path)
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