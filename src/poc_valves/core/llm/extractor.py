"""Phase 1 (direct-field extraction) and Phase 2 (judgment-field
derivation) LLM calls for the SV2 three-phase pipeline.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from ..config import get_prompt, load_reference_fields
from ..models import PartialSV2ValveDatasheet, SV2ValveDatasheet
from .client import LLMClient

logger = logging.getLogger(__name__)


@dataclass
class FieldSpecLite:
    """Lightweight field specification for extraction."""

    sv2_field_name: str
    source_hint: str
    logic_type: Optional[str] = None


def load_field_registry() -> list[FieldSpecLite]:
    """Load field registry from reference.yml."""
    registry: list[FieldSpecLite] = []
    for entry in load_reference_fields():
        source = (
            entry.get("source", {})
            if isinstance(entry.get("source"), dict)
            else {}
        )
        registry.append(
            FieldSpecLite(
                sv2_field_name=str(entry.get("sv2_field_name") or "").strip(),
                source_hint=str(
                    source.get("enquiry_field_name") or ""
                ).strip(),
                logic_type=str(entry.get("extraction_type") or "").strip(),
            )
        )
    return registry


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
# focused LLM call per tag in Phase 2 (see SV2FieldExtractor.derive_judgment_fields).
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


# Row 1 of the enquiry DS lays out "Tag No." | "PID" | <value> | <P&ID doc
# ref> in a single visual row, where "PID" is the sub-column header (short
# for the P&ID document the tag is cross-referenced to), not part of the
# tag number itself. In linear OCR/table text this header sits directly
# before the value (e.g. "PID 1803-FV-10401"), so it sometimes leaks into
# the extracted Tag No — strip a leading "PID" token before it becomes the
# tag name and, downstream, the Excel sheet name.
_TAG_NO_PID_PREFIX_RE = re.compile(r"^\s*PID\b[\s:\-]*", re.IGNORECASE)

# The source enquiry datasheet uses a comma as the decimal separator
# throughout (temperature, pressure, flow, density, viscosity, etc. —
# e.g. "10,5", "6,8", "0,276"), not a period. Downstream numeric parsing
# (postprocess._parse_numeric) and the reference guide's documented output
# format both expect periods, so this is normalized as soon as a value is
# extracted rather than at every call site that later parses it.
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")


def _normalize_tag_no(value: Optional[str]) -> Optional[str]:
    """Strip a leading 'PID' column-header token from an extracted tag
    number, if present. See _TAG_NO_PID_PREFIX_RE for why this happens.
    """
    if not value:
        return value
    return _TAG_NO_PID_PREFIX_RE.sub("", value).strip()


def _normalize_decimal_commas(value: Optional[str]) -> Optional[str]:
    """Replace a comma decimal separator between digits (e.g. '10,5') with
    a period ('10.5'). Leaves commas that aren't between two digits alone.
    """
    if not value:
        return value
    return _DECIMAL_COMMA_RE.sub(".", value)


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


class SV2FieldExtractor:
    """Runs the two LLM-backed extraction phases of the SV2 pipeline.

    Phase 1 (``extract_page``) pulls direct + auxiliary field values,
    already normalized and mapped to SV2ValveDatasheet attribute names, out
    of a single page's text/tables/image. Phase 2 (``derive_judgment_fields``)
    computes the 11 judgment-based derived fields from a tag's Phase-1
    output.
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        registry: Optional[list[FieldSpecLite]] = None,
    ) -> None:
        self.llm = llm_client or LLMClient()
        self.registry = registry if registry is not None else load_field_registry()
        self._name_to_attr = _build_sv2_field_name_to_attr_map()
        self._direct_fields = [
            f
            for f in self.registry
            if (not f.logic_type) or f.logic_type.lower() == "direct"
        ] + AUXILIARY_SOURCE_FIELDS
        reference_by_name = {
            str(e.get("sv2_field_name") or "").strip(): e
            for e in load_reference_fields()
        }
        self._judgment_field_logic_lines = [
            f"- {sv2_name} ({attr}): "
            f"{str(reference_by_name.get(sv2_name, {}).get('logic') or '').strip()}"
            for attr, sv2_name in JUDGMENT_FIELD_ATTR_TO_NAME.items()
        ]

    def extract_page(
        self,
        page_text: str,
        page_number: int,
        *,
        image_b64: str = "",
        tables: Optional[list[dict]] = None,
        guide: Optional[str] = None,
    ) -> tuple[dict[str, object], dict[str, Optional[str]], dict[str, int], float]:
        """Phase 1: extract direct + auxiliary fields from one page.

        Returns ``(direct_values, aux_values, usage, cost)`` where
        ``direct_values`` is keyed by SV2ValveDatasheet attribute name
        (ready to build a ``PartialSV2ValveDatasheet``) and ``aux_values``
        holds the raw auxiliary ``raw_*`` values Phase 2 needs.
        """
        field_list = "\n".join(
            f"- {f.sv2_field_name}: look for '{f.source_hint}'"
            for f in self._direct_fields
        )

        user_parts: list[dict] = [
            {
                "type": "text",
                "text": (
                    f"Extract these fields:\n{field_list}\n\n"
                    f"OCR text for this page:\n{page_text}"
                ),
            }
        ]
        if tables:
            tables_text = "\n\n".join(
                f"Table {i + 1}: {json.dumps(t)}"
                for i, t in enumerate(tables)
            )
            user_parts.append(
                {
                    "type": "text",
                    "text": f"Detected tables on this page:\n{tables_text}",
                }
            )
        if image_b64 and str(image_b64).strip():
            try:
                base64.b64decode(image_b64, validate=True)
                user_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}"
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
                0, {"type": "text", "text": f"Guide for extraction:\n{guide}"}
            )

        page_extraction, usage, cost = self.llm.chat_parse(
            system_prompt=get_prompt("data_extraction.system_prompt"),
            user_content=user_parts,
            response_format=PageExtraction,
            log_label=f"LLM usage page {page_number}",
        )

        direct_values: dict[str, object] = {}
        aux_values: dict[str, Optional[str]] = {}
        for ef in page_extraction.fields:
            if ef.raw_value is None:
                continue
            value = _normalize_decimal_commas(ef.raw_value)
            attr = self._name_to_attr.get(ef.field_name)
            if attr is not None:
                if attr == "tag_no":
                    value = _normalize_tag_no(value)
                direct_values[attr] = value
            elif ef.field_name.startswith("raw_"):
                aux_values[ef.field_name] = value

        if not direct_values.get("tag_no"):
            direct_values["tag_no"] = (
                _normalize_tag_no(page_extraction.tag_number)
                or f"page-{page_number}"
            )

        return direct_values, aux_values, usage, cost

    def derive_judgment_fields(
        self,
        tag: PartialSV2ValveDatasheet,
        aux: dict[str, Optional[str]],
    ) -> tuple[DerivedJudgmentFields, dict[str, int], float]:
        """Phase 2 (LLM half): compute the 11 judgment-based derived fields
        from this tag's Phase-1 direct-field values and auxiliary raw source
        values only — no raw OCR text/tables are re-read here.
        """
        direct_fields_json = json.dumps(
            {k: v for k, v in tag.model_dump().items() if v is not None},
            ensure_ascii=False,
        )
        auxiliary_fields_json = json.dumps(
            {k: v for k, v in aux.items() if v}, ensure_ascii=False
        )

        user_prompt = get_prompt(
            "derived_field_judgment.user_prompt",
            direct_fields_json=direct_fields_json,
            auxiliary_fields_json=auxiliary_fields_json,
            field_logic_block="\n".join(self._judgment_field_logic_lines),
        )

        return self.llm.chat_parse(
            system_prompt=get_prompt("derived_field_judgment.system_prompt"),
            user_content=user_prompt,
            response_format=DerivedJudgmentFields,
            log_label=f"LLM usage (Phase 2 judgment) tag {tag.tag_no}",
        )
