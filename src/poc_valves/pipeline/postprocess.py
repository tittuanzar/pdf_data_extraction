"""
Post-extraction processing for SV2 valve datasheets.

Applies engineered defaults and deterministic derivation rules defined in
the reference guide.  Called after the LLM extraction step to fill in
fields that are either:
  - **Engineered**: not present in the enquiry PDF at all (come from
    project POs, vendor standards, etc.)
  - **Derived**: computable from other extracted fields via fixed rules.

All logic is derived from the reference_guide.xlsx "Logic" column.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..pydantic_output import SV2ValveDatasheet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engineered defaults — fields that are NEVER in the enquiry PDF.
# Keyed by pydantic field name on SV2ValveDatasheet.
# Only applied when the field is still None after LLM extraction.
# ---------------------------------------------------------------------------
ENGINEERED_DEFAULTS: dict[str, str] = {
    "painting_scheme": (
        "KSB MIL standard: Body — SPECIAL | Actuator — SPECIAL. "
        "Painting procedure reference from project PO."
    ),
    "cable_gland_type": "Double compression type",
}


# ---------------------------------------------------------------------------
# Derived-field functions
# ---------------------------------------------------------------------------

def derive_actuator_colour(tag: SV2ValveDatasheet) -> Optional[str]:
    """Derive actuator housing colour from actuator action / air failure.

    Reference guide logic (Actuator Colour, serial 58):
        Fail Open  → GREEN
        Fail Close → RED
        Body always GREY (IT 228HS).

    The source field ``actuator_action_air_failure`` typically contains
    one of: "Air to Close (ATC)", "Air to Open (ATO)", "Fail Open",
    "Fail Close", or similar phrasing.
    """
    src = (tag.actuator_action_air_failure or "").strip().lower()
    if not src:
        return None

    # Fail-Open variants: ATC (air-to-close = valve opens on air loss)
    if any(kw in src for kw in ("fail open", "atc", "air to close")):
        return "GREEN"

    # Fail-Close variants: ATO (air-to-open = valve closes on air loss)
    if any(kw in src for kw in ("fail close", "ato", "air to open")):
        return "RED"

    return None


def derive_cable_gland_certification(tag: SV2ValveDatasheet) -> Optional[str]:
    """Derive cable gland certification from area classification.

    Reference guide logic (Cable Gland Certification, serial 60):
        Zone 1 IIC → Explosion Proof: Ex d IIA, IIB, IIC.
        Material: SS 304 + PVC Hood.

    Since there is no standalone "area classification" field, we inspect
    ``positioner_certification`` which is copied from the enquiry's area
    classification row.  If that field mentions a Zone, we derive the
    cable gland certification from it.
    """
    # Check positioner_certification for zone info (primary source)
    src = (tag.positioner_certification or "").strip().lower()

    # Also check ibr_applicability / fluid_name / service for hints
    if not src:
        src = (
            (tag.service or "")
            + " "
            + (tag.fluid_name or "")
        ).strip().lower()

    if not src:
        return None

    # Zone 1 or Zone 2 with IIC
    if re.search(r"zone\s*1", src):
        if "iic" in src:
            return "Ex d IIA, IIB, IIC (Zone 1)"
        return "Ex d IIA, IIB, IIC (Zone 1)"

    if re.search(r"zone\s*2", src):
        if "iic" in src:
            return "Ex e IIA, IIB, IIC (Zone 2)"
        return "Ex e IIA, IIB, IIC (Zone 2)"

    # Hazardous area mentioned generically
    if "hazardous" in src or "zone" in src:
        return "Ex d IIA, IIB, IIC"

    return None


# ---------------------------------------------------------------------------
# Main postprocessing entry point
# ---------------------------------------------------------------------------

def postprocess_tag(
    tag: SV2ValveDatasheet,
    *,
    use_engineered_defaults: bool = True,
) -> SV2ValveDatasheet:
    """Apply post-extraction rules to a single SV2ValveDatasheet tag.

    Parameters
    ----------
    tag:
        The tag as returned by the LLM extraction step.
    use_engineered_defaults:
        When *False*, only derived fields are populated; engineered
        defaults (which are organisation-specific) are skipped.

    Returns
    -------
    SV2ValveDatasheet
        A (possibly mutated) tag with derived/engineered fields filled in.
    """
    updates: dict[str, str] = {}

    # --- Engineered defaults ---
    if use_engineered_defaults:
        for field_name, default_value in ENGINEERED_DEFAULTS.items():
            current = getattr(tag, field_name, None)
            if current is None:
                updates[field_name] = default_value
                logger.debug("Engineered default applied: %s = %s", field_name, default_value)

    # --- Derived fields ---
    # Actuator colour
    if tag.actuator_colour is None:
        derived = derive_actuator_colour(tag)
        if derived:
            updates["actuator_colour"] = derived
            logger.debug("Derived actuator_colour = %s", derived)

    # Cable gland certification
    if tag.cable_gland_certification is None:
        derived = derive_cable_gland_certification(tag)
        if derived:
            updates["cable_gland_certification"] = derived
            logger.debug("Derived cable_gland_certification = %s", derived)

    if updates:
        tag = tag.model_copy(update=updates)

    return tag


def postprocess_tags(
    tags: list[SV2ValveDatasheet],
    *,
    use_engineered_defaults: bool = True,
) -> list[SV2ValveDatasheet]:
    """Apply postprocessing to all tags in a ParsedOutput.tags list."""
    return [
        postprocess_tag(t, use_engineered_defaults=use_engineered_defaults)
        for t in tags
    ]
