"""
Skeleton for the field-extraction pipeline described in the reference guide.
Three stages: (1) build a field registry from the xlsx, (2) call the LLM for
extraction of Direct fields only, (3) apply Derived/Engineered logic in code.
"""

import json
import logging
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import pandas as pd
from openai import OpenAI
import dotenv
import os
import sys

dotenv.load_dotenv()

# Diagnostic info: which .env was found and which python is running (do not print the key)
env_file = dotenv.find_dotenv()
python_exec = sys.executable

# Prefer explicit env var values; don't overwrite with None.
api_key = os.getenv('OPENAI_API_KEY') or os.getenv('OPENAI_ADMIN_KEY')
if not api_key:
    raise RuntimeError(
        f"Missing OpenAI API key. Python: '{python_exec}'. dotenv file: '{env_file or 'none'}'. "
        "Set the OPENAI_API_KEY or OPENAI_ADMIN_KEY environment variable or add it to a .env file."
    )

client = OpenAI(api_key=api_key)

# module logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    # basic config if not configured by the app
    logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# 1. Field registry — parsed once from the reference guide, cached to disk.
# ---------------------------------------------------------------------------

class LogicType(str, Enum):
    DIRECT = "Direct"
    DERIVED = "Derived"
    ENGINEERED = "Engineered"

class Requirement(str, Enum):
    MANDATORY = "Mandatory"
    OPTIONAL = "Optional"
    CONDITIONAL = "Conditional"

class FieldSpec(BaseModel):
    sv2_field_name: str
    source_hint: str          # "Enquiry Field Name" column
    requirement: Requirement
    logic_type: LogicType
    mapping_rule: str         # the "Logic" column text, verbatim
    similar_terms: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Excel wraps long headers onto two lines inside a single cell, so
    pandas reads them back with an embedded '\\n' (e.g. 'Field\\nRequirement').
    Collapse whitespace/newlines so headers match what you'd expect from
    reading the sheet visually."""
    df = df.copy()
    df.columns = [" ".join(str(c).split()) for c in df.columns]
    return df


def load_field_registry(xlsx_path: str) -> list[FieldSpec]:
    df = pd.read_excel(xlsx_path, sheet_name="Sheet1")
    df = _normalize_columns(df)
    registry = []
    for _, row in df.iterrows():
        registry.append(FieldSpec(
            sv2_field_name=row["Sv2 Field Name"],
            source_hint=row["Enquiry Field Name (Customer Spec)"],
            requirement=Requirement(row["Field Requirement"]),
            logic_type=LogicType(row["Mapping Category"]),
            mapping_rule=str(row["Logic"]),
            similar_terms=[t.strip() for t in str(row.get("Similar Field Terminologies", "")).split(";") if t.strip()],
            notes=None if pd.isna(row.get("Notes and Validations")) else str(row.get("Notes and Validations")),
        ))
    return registry


# ---------------------------------------------------------------------------
# 2. LLM extraction — ONLY for Direct fields. Ask for the raw value plus the
#    page/quote it came from, so every field is auditable and the model is
#    not asked to do arithmetic or apply business rules.
# ---------------------------------------------------------------------------

class ExtractedField(BaseModel):
    field_name: str
    raw_value: Optional[str]      # null if genuinely not present on the page
    source_quote: Optional[str]   # short verbatim snippet as evidence
    page_number: Optional[int]
    confidence: float             # 0-1, model's own estimate

class PageExtraction(BaseModel):
    tag_number: Optional[str]     # datasheets often cover multiple tags/instruments
    fields: list[ExtractedField]


def extract_direct_fields(registry: list[FieldSpec], page_image_b64: str, page_text: str, page_number: int) -> PageExtraction:
    direct_fields = [f for f in registry if f.logic_type == LogicType.DIRECT]
    field_list = "\n".join(f"- {f.sv2_field_name}: look for '{f.source_hint}'" for f in direct_fields)

    try:
        response = client.chat.completions.parse(
            model="gpt-4.1-mini-2025-04-14",  # any vision-capable model
            messages=[
                {"role": "system", "content": (
                    "You extract field values verbatim from an engineering datasheet page. "
                    "Only report a value if you can point to where it appears — never infer "
                    "or calculate. If a field is not present on this page, set raw_value to null. "
                    "Include a short quote as evidence and the field's confidence."
                )},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Extract these fields:\n{field_list}\n\nOCR text for this page:\n{page_text}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_image_b64}"}},
                ]},
            ],
            response_format=PageExtraction,
        )
    except Exception as exc:
        logger.exception("LLM extraction failed in extract_direct_fields for page %s", page_number)
        raise RuntimeError(f"LLM extraction failed for page {page_number}: {exc}") from exc

    # defensive checks
    if not hasattr(response, "choices") or not response.choices:
        logger.error("LLM returned no choices for page %s: %r", page_number, getattr(response, '__dict__', repr(response)))
        raise RuntimeError(f"LLM returned no choices for page {page_number}")

    # attempt to access parsed output, otherwise log raw
    if not hasattr(response.choices[0].message, "parsed"):
        logger.error("LLM response missing parsed attribute for page %s; raw response: %r", page_number, getattr(response, '__dict__', repr(response)))
        raise RuntimeError(f"LLM response could not be parsed for page {page_number}")

    result = response.choices[0].message.parsed
    for f in result.fields:
        f.page_number = page_number
    return result


def extract_direct_fields_with_context(
    registry: list[FieldSpec],
    page_image_b64: str,
    page_text: str,
    page_number: int,
    guide: Optional[str] = None,
    tables: Optional[list[dict]] = None,
    debug: bool = False,
) -> PageExtraction | tuple[PageExtraction, object]:
    """Like `extract_direct_fields` but allows passing an extraction guide
    and any table content found on the page. This is intended for the
    SV2-style pipeline where the caller provides tag-related context
    (images + extracted text + table contents) to the LLM.
    """
    direct_fields = [f for f in registry if f.logic_type == LogicType.DIRECT]
    field_list = "\n".join(f"- {f.sv2_field_name}: look for '{f.source_hint}'" for f in direct_fields)

    user_parts = []
    user_parts.append({"type": "text", "text": f"Extract these fields:\n{field_list}\n\nOCR text for this page:\n{page_text}"})
    if tables:
        # include a compact representation of tables for LLM context
        tables_text = "\n\n".join(
            f"Table {i+1}: {json.dumps(t)}" for i, t in enumerate(tables)
        )
        user_parts.append({"type": "text", "text": f"Detected tables on this page:\n{tables_text}"})
    user_parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_image_b64}"}})

    if guide:
        user_parts.insert(0, {"type": "text", "text": f"Guide for extraction:\n{guide}"})

    try:
        response = client.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": (
                    "You extract field values verbatim from an engineering datasheet page. "
                    "Only report a value if you can point to where it appears — never infer "
                    "or calculate. If a field is not present on this page, set raw_value to null. "
                    "Include a short quote as evidence and the field's confidence."
                )},
                {"role": "user", "content": user_parts},
            ],
            response_format=PageExtraction,
        )
    except Exception as exc:
        logger.exception("LLM extraction failed in extract_direct_fields_with_context for page %s", page_number)
        raise RuntimeError(f"LLM extraction failed for page {page_number}: {exc}") from exc

    if not hasattr(response, "choices") or not response.choices:
        logger.error("LLM returned no choices for page %s; raw response: %r", page_number, getattr(response, '__dict__', repr(response)))
        raise RuntimeError(f"LLM returned no choices for page {page_number}")

    if not hasattr(response.choices[0].message, "parsed"):
        logger.error("LLM response missing parsed attribute for page %s; raw response: %r", page_number, getattr(response, '__dict__', repr(response)))
        raise RuntimeError(f"LLM response could not be parsed for page {page_number}")

    result = response.choices[0].message.parsed
    for f in result.fields:
        f.page_number = page_number

    if debug:
        return result, response
    return result


def run_pipeline_sv2(registry: list[FieldSpec], pages: list[dict], guide: Optional[str] = None, debug: bool = False) -> dict:
    """Run a lightweight SV2-style pipeline over provided pages.

    `pages` should be a list of dicts with keys: `page_number`,
    `image_b64`, `text`, and optional `tables` (list of dicts).

    This function performs per-page direct extraction (via LLM with the
    provided guide/tables), then aggregates the results into a combined
    record. It intentionally does not mutate any global state or the
    existing `run_pipeline()` implementation.
    """
    per_page_results = []
    combined_best: dict[str, dict] = {}

    for p in pages:
        page_num = int(p.get("page_number", 0))
        page_text = p.get("text", "")
        image_b64 = p.get("image_b64", "")
        tables = p.get("tables")

        extracted = extract_direct_fields_with_context(registry, image_b64, page_text, page_num, guide=guide, tables=tables, debug=debug)
        raw_resp = None
        if debug and isinstance(extracted, tuple):
            page_extraction, raw_resp = extracted
        else:
            page_extraction = extracted

        page_dict = page_extraction.dict()
        if debug:
            page_dict["raw_llm"] = getattr(raw_resp, '__dict__', repr(raw_resp))

        per_page_results.append(page_dict)

        # Naive "first non-null wins" aggregation into combined_best
        for ef in page_extraction.fields:
            name = ef.field_name
            if ef.raw_value is None:
                continue
            if name not in combined_best:
                combined_best[name] = {
                    "raw_value": ef.raw_value,
                    "source": "direct",
                    "page_number": ef.page_number,
                    "confidence": ef.confidence,
                    "source_quote": ef.source_quote,
                }

    result = {
        "pages": per_page_results,
        "combined": combined_best,
    }
    return result


# ---------------------------------------------------------------------------
# 3. Deterministic derivation — apply the documented formulas in plain code
#    instead of asking the LLM to compute them. Example: outlet pressure.
# ---------------------------------------------------------------------------

def derive_outlet_pressure(inlet_pressure: float, pressure_drop: float) -> float:
    """Outlet Pressure = Inlet Pressure - Pressure Drop (per the guide's Logic column)."""
    return round(inlet_pressure - pressure_drop, 3)

def derive_specific_gravity(density_kg_m3: float) -> float:
    """SG = density / 1000 for liquid service."""
    return round(density_kg_m3 / 1000, 3)

# Engineered fields ("By Vendor") never come from the document. Apply your
# organization's standard defaults here (or route to a human), keyed off
# service type / area classification / etc. Never let the LLM invent them.
ENGINEERED_DEFAULTS = {
    "Positioner Make": "METSO",
    "Positioner Model": "ND9103-HX-T",
    "Cable Gland Type": "Double compression type",
    # ...
}


# ---------------------------------------------------------------------------
# 4. Validation — run the "Notes and Validations" checks as code assertions,
#    flagging anything that fails for human review rather than silently
#    accepting it.
# ---------------------------------------------------------------------------

def validate(record: dict) -> list[str]:
    warnings = []
    if record.get("inlet_pipe_size") and record.get("outlet_pipe_size"):
        if record["inlet_pipe_size"] != record["outlet_pipe_size"]:
            warnings.append("Inlet/outlet size differ — expander/reducer required, flag for engineer review.")
    if record.get("outlet_pressure", 0) < 0:
        warnings.append("Negative outlet pressure — check gauge vs absolute pressure units.")
    return warnings


if __name__ == "__main__":
    registry = load_field_registry("reference_guide.xlsx")
    print(f"Loaded {len(registry)} fields "
          f"({sum(f.logic_type == LogicType.DIRECT for f in registry)} direct, "
          f"{sum(f.logic_type == LogicType.DERIVED for f in registry)} derived, "
          f"{sum(f.logic_type == LogicType.ENGINEERED for f in registry)} engineered)")