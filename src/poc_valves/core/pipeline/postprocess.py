"""
Post-extraction processing for SV2 valve datasheets.

Applies engineered defaults and deterministic derivation rules defined
in the reference guide.  Called after the LLM extraction step to fill
in fields that are either:
  - **Engineered**: not present in the enquiry PDF at all (come from
    project POs, vendor standards, etc.)
  - **Derived**: computable from other extracted fields via fixed rules.

All logic is derived from the reference_guide.xlsx "Logic" column.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from ..models import PartialSV2ValveDatasheet, SV2ValveDatasheet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_numeric(value: Optional[str]) -> Optional[float]:
    """Extract the leading numeric portion from a string like '6.8 kgf/cm²-g'."""
    if not value:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", value.strip())
    if m:
        try:
            return float(m.group())
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Engineered defaults — fields that are NEVER in the enquiry PDF.
# Keyed by pydantic field name on SV2ValveDatasheet.
# Only applied when the field is still None after LLM extraction.
# ---------------------------------------------------------------------------
def _default_guiding(tag: PartialSV2ValveDatasheet) -> str:
    """Default for Guiding (serial 41).

    Classified 'engineered' in the reference guide, but unlike the other
    four engineered fields its logic is service-dependent rather than a
    flat constant: steam service uses Cage Guiding, everything else uses
    Heavy Top Guiding (vendor-standard default).
    """
    phase = (tag.fluid_phase or "").strip().lower()
    return "Cage Guiding" if phase == "steam" else "Heavy Top Guiding"


ENGINEERED_DEFAULTS: dict[str, str | Callable[[PartialSV2ValveDatasheet], str]] = {
    # serial 57 — KSB MIL standard painting/coating scheme from project PO.
    "painting_scheme": (
        "KSB MIL standard: Body — SPECIAL | "
        "Actuator — SPECIAL. "
        "Painting procedure reference from project PO."
    ),
    # serial 59 — vendor-standard cable gland construction.
    "cable_gland_type": "Double compression type",
    # serial 48 — standard smart positioner for modulating control valves.
    "positioner_type": "Smart Single Acting (HART)",
    # serial 45 — reference guide states this cannot be determined from the
    # enquiry DS alone; placeholder MUST be verified by the engineer.
    "bench_range": "",
    # serial 41 — service-dependent, see _default_guiding.
    "guiding": _default_guiding,
}


# ---------------------------------------------------------------------------
# Derived-field functions
# ---------------------------------------------------------------------------

def derive_actuator_colour(
    tag: SV2ValveDatasheet,
) -> Optional[str]:
    """Derive actuator housing colour from actuator action / air failure.

    Reference guide logic (Actuator Colour, serial 58):
        Fail Open  -> GREEN
        Fail Close -> RED
        Body always GREY (IT 228HS).

    The source field ``actuator_action_air_failure`` typically
    contains one of: "Air to Close (ATC)", "Air to Open (ATO)",
    "Fail Open", "Fail Close", or similar phrasing.
    """
    src = (
        tag.actuator_action_air_failure or ""
    ).strip().lower()
    if not src:
        return None

    # Fail-Open variants: ATC (air-to-close = valve opens on air loss)
    if any(
        kw in src
        for kw in ("fail open", "atc", "air to close")
    ):
        return "GREEN"

    # Fail-Close variants: ATO (air-to-open = valve closes on air loss)
    if any(
        kw in src
        for kw in ("fail close", "ato", "air to open")
    ):
        return "RED"

    return None


def derive_cable_gland_certification(
    tag: SV2ValveDatasheet,
    aux: Optional[dict] = None,
) -> Optional[str]:
    """Derive cable gland certification from area classification.

    Reference guide logic (Cable Gland Certification, serial 60):
        Zone 1 IIC -> Explosion Proof: Ex d IIA, IIB, IIC.
        Material: SS 304 + PVC Hood.

    Primary source is the "Area Classification" auxiliary value (enquiry
    Row 4), which reliably contains literal "Zone N ..." text. Earlier
    versions of this function inspected ``positioner_certification``
    instead, but that field's typical phrasing (e.g. "Intrinsic Safe
    Ex ia IIC, T6, WP IP66") rarely contains the word "Zone" — so it is
    kept only as a fallback, not the primary source.
    """
    src = ((aux or {}).get("raw_area_classification") or "").strip().lower()

    # Fallback: positioner_certification (rarely mentions "Zone" directly)
    if not src:
        src = (
            tag.positioner_certification or ""
        ).strip().lower()

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


def _is_gas_phase(tag: SV2ValveDatasheet) -> bool:
    """Check if fluid phase is gas/vapour."""
    phase = (tag.fluid_phase or "").strip().lower()
    return "gas" in phase or "vapor" in phase or "vapour" in phase


def _is_steam_phase(tag: SV2ValveDatasheet) -> bool:
    """Check if fluid phase is steam."""
    phase = (tag.fluid_phase or "").strip().lower()
    return "steam" in phase


def derive_compressibility_factor_z(
    tag: SV2ValveDatasheet,
) -> Optional[str]:
    """Derive compressibility factor Z for gas services.

    Reference guide logic (Compressibility Factor Z, serial 28):
        - Gas/Vapour: use Z from enquiry, default 1.0 if missing.
        - Liquid / Steam: N/A — leave blank.
        - For ideal gases Z = 1.0.
    """
    if not _is_empty_or_placeholder(tag.compressibility_factor_z):
        return None  # already populated

    if _is_gas_phase(tag):
        return "1.0"

    # Liquid / steam — not applicable
    return "N/A (liquid)"


def derive_specific_heats_ratio_k(
    tag: SV2ValveDatasheet,
) -> Optional[str]:
    """Derive specific heats ratio K (gamma) for gas/steam services.

    Reference guide logic (Specific Heats Ratio K, serial 29):
        - Gas: default 1.4 (conservative for diatomic gases).
        - Steam: default 1.3.
        - Liquid: N/A.
    """
    if not _is_empty_or_placeholder(tag.specific_heats_ratio_k):
        return None  # already populated

    if _is_steam_phase(tag):
        return "1.3"

    if _is_gas_phase(tag):
        return "1.4"

    # Liquid — not applicable
    return "N/A (liquid)"


_DASH_PLACEHOLDER_RE = re.compile(r"^[-‐-―]+$")


def _is_empty_or_placeholder(value: Optional[str]) -> bool:
    """Check if a field is None, blank, a zero, or a dash placeholder.

    Enquiry PDFs commonly render "not applicable" cells as a bare
    dash (``-``, ``--``, en/em-dash); the LLM extractor copies that
    literal through as a non-``None`` value, so it must be treated as
    empty here or downstream derivation rules never fire.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    if _DASH_PLACEHOLDER_RE.match(stripped):
        return True
    num = _parse_numeric(stripped)
    if num is not None and num == 0.0:
        return True
    return False


def derive_outlet_pressure(
    tag: SV2ValveDatasheet,
    inlet_field: str,
    drop_field: str,
) -> Optional[str]:
    """Outlet Pressure = Inlet Pressure - Pressure Drop.

    Reference guide logic (Outlet Pressure, serial 17/18/19):
        Outlet Pressure = Inlet Pressure - Pressure Drop
        for each case (MAX, NOR, MIN).

    If the result is negative, the enquiry may use absolute rather
    than gauge pressure — flagged in logs for engineer confirmation.
    """
    inlet_val = _parse_numeric(getattr(tag, inlet_field, None))
    drop_val = _parse_numeric(getattr(tag, drop_field, None))

    if inlet_val is None or drop_val is None:
        return None

    result = round(inlet_val - drop_val, 3)

    if result < 0:
        logger.warning(
            "Negative outlet pressure derived from %s - %s = %s. "
            "Check whether the enquiry uses absolute rather than "
            "gauge pressure.",
            inlet_val,
            drop_val,
            result,
        )

    return f"{result} kgf/cm2-g"


# ---------------------------------------------------------------------------
# Phase 2 deterministic derived-field functions (three-phase pipeline).
#
# These consume the auxiliary raw source values captured alongside the 34
# direct fields in Phase 1 (see sv2_pipeline.AUXILIARY_SOURCE_FIELDS) in
# addition to already-extracted/derived fields on the tag itself.
# ---------------------------------------------------------------------------

def derive_fluid_phase(aux: dict) -> Optional[str]:
    """Derive Fluid Phase (serial 4) from Upstream Condition (Row 15).

    Reference guide logic: Water/Liquid/Condensate -> Liquid;
    Steam/LP Steam/HP Steam -> Steam; Gas/Vapor/Air/Nitrogen -> Gas.
    """
    src = (aux.get("raw_upstream_condition") or "").strip().lower()
    if not src:
        return None
    if "steam" in src:
        return "Steam"
    if any(kw in src for kw in ("water", "liquid", "condensate")):
        return "Liquid"
    if any(
        kw in src
        for kw in ("gas", "vapor", "vapour", "air", "nitrogen")
    ):
        return "Gas"
    return None


def derive_specific_gravity_or_molecular_weight(
    tag: PartialSV2ValveDatasheet, aux: dict
) -> Optional[str]:
    """Derive Specific Gravity / Molecular Weight (serial 26).

    Reference guide logic: for liquids, specific gravity is the inlet
    density (kg/m3) relative to water (divide by 1000); for gas, the
    molecular weight is carried through as-is; steam is not applicable.
    Requires ``fluid_phase`` to already be resolved.
    """
    phase = (tag.fluid_phase or "").strip().lower()
    raw = aux.get("raw_inlet_density_or_mw")
    if not raw:
        return None
    if phase == "liquid":
        density = _parse_numeric(raw)
        if density is None:
            return None
        return f"SG = {round(density / 1000.0, 3)}"
    if phase == "gas":
        return raw.strip()
    if phase == "steam":
        return "N/A (steam)"
    return None


def derive_seat_leakage_class(aux: dict) -> Optional[str]:
    """Derive Seat Leakage Class (serial 40) from Tightness Requirements
    (Row 7), mapping the customer's ANSI class to FCI 70.2 nomenclature.
    """
    src = (aux.get("raw_tightness_requirements") or "").strip().lower()
    if not src:
        return None
    m = re.search(r"ansi\s*(vi|iv|v)\b", src)
    if not m:
        return None
    return f"{m.group(1).upper()} (FCI 70.2)"


def derive_actuator_action_air_failure(aux: dict) -> Optional[str]:
    """Derive Actuator Action / Air Failure (serial 43).

    Reference guide logic: Power Failure Position (Row 9) is mapped —
    Fail Open -> Air to Close (ATC); Fail Close -> Air to Open (ATO).
    Air Failure Valve (Row 52) is used as a cross-check/fallback source.
    """
    src = (
        aux.get("raw_power_failure_position")
        or aux.get("raw_air_failure_valve_action")
        or ""
    ).strip().lower()
    if not src:
        return None
    if "open" in src:
        return "Air to Close (ATC)"
    if "close" in src:
        return "Air to Open (ATO)"
    return None


def derive_shut_off_pressure(
    tag: PartialSV2ValveDatasheet,
) -> Optional[str]:
    """Derive Shut-off Pressure (serial 46).

    Reference guide logic: shut-off pressure equals the maximum design
    pressure of the valve (Design Pressure MAX, already a direct field).
    """
    return tag.design_pressure_max


def derive_airset_set_pressure_filter(
    tag: PartialSV2ValveDatasheet,
) -> Optional[str]:
    """Derive Airset Set Pressure / Filter (serial 56).

    Reference guide logic: airset set pressure follows the derived Supply
    Pressure (serial 44); filter element is a fixed 5-micron standard.
    Requires ``supply_pressure`` to already be resolved — this only
    happens after the Phase-2 LLM-judgment call, so this function belongs
    in the post-LLM derivation stage, not the pre-LLM one.
    """
    if not tag.supply_pressure:
        return None
    return f"Set at {tag.supply_pressure} | 5 Micron Filter | Gauge: Yes"


def derive_ndt_igc_test(
    tag: PartialSV2ValveDatasheet,
) -> Optional[str]:
    """Derive NDT — IGC Test (serial 64).

    Reference guide logic: Intergranular Corrosion testing (ASTM A262
    Practice E) is triggered when an austenitic stainless-steel trim/seat/
    plug material is used.
    """
    src = f"{tag.plug_material or ''} {tag.seat_material or ''}".lower()
    if any(
        kw in src
        for kw in ("ss316", "ss 316", "cf8m", "cf3m", "304", "316")
    ):
        return "IGC Test per ASTM A262 Practice E — Applicable"
    return "Not Applicable"


def derive_peso_certificate(aux: dict) -> Optional[str]:
    """Derive PESO Certificate (serial 66).

    Reference guide logic: a PESO certificate is required for all
    electrical accessories when the area classification names a
    hazardous zone.
    """
    src = (aux.get("raw_area_classification") or "").strip().lower()
    if not src:
        return None
    if "zone" in src:
        return "PESO Certificate for All Electrical Accessories"
    return "PESO Certificate Not Required"


# Derived fields (serials 62, 63, 65) that carry an industry-standard
# default with no per-tag variance described in the reference guide —
# unlike ENGINEERED_DEFAULTS these are always applied, not gated by
# ``use_engineered_defaults``, since they express testing/quality
# requirements rather than organisation-specific defaults.
STANDARD_DERIVED_DEFAULTS: dict[str, str] = {
    "ndt_rt_extent": (
        "10% RT per lot — Body/Bonnet/Comp. Flange/Exp/LNP"
    ),
    "ndt_pmi_test": "PMI Test (Std) applicable for trim parts",
    "valve_operating_signature": (
        "Valve operating signature in the form of CD"
    ),
}


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
        A (possibly mutated) tag with derived/engineered fields
        filled in.
    """
    updates: dict[str, str] = {}

    # --- Engineered defaults ---
    if use_engineered_defaults:
        for field_name, default_value in (
            ENGINEERED_DEFAULTS.items()
        ):
            current = getattr(tag, field_name, None)
            if current is None:
                updates[field_name] = default_value
                logger.debug(
                    "Engineered default applied: %s = %s",
                    field_name,
                    default_value,
                )

    # --- Derived fields ---
    # Actuator colour
    if tag.actuator_colour is None:
        derived = derive_actuator_colour(tag)
        if derived:
            updates["actuator_colour"] = derived
            logger.debug(
                "Derived actuator_colour = %s", derived
            )

    # Cable gland certification
    if tag.cable_gland_certification is None:
        derived = derive_cable_gland_certification(tag)
        if derived:
            updates["cable_gland_certification"] = derived
            logger.debug(
                "Derived cable_gland_certification = %s",
                derived,
            )

    # Compressibility Factor Z — default 1.0 for gas services
    if _is_empty_or_placeholder(tag.compressibility_factor_z):
        derived = derive_compressibility_factor_z(tag)
        if derived:
            updates["compressibility_factor_z"] = derived
            logger.debug(
                "Derived compressibility_factor_z = %s", derived
            )

    # Specific Heats Ratio K — 1.4 for gas, 1.3 for steam
    if _is_empty_or_placeholder(tag.specific_heats_ratio_k):
        derived = derive_specific_heats_ratio_k(tag)
        if derived:
            updates["specific_heats_ratio_k"] = derived
            logger.debug(
                "Derived specific_heats_ratio_k = %s", derived
            )

    # Outlet Pressure (MAX) = Inlet Pressure (MAX) - Pressure Drop (MAX)
    if _is_empty_or_placeholder(tag.outlet_pressure_max):
        derived = derive_outlet_pressure(
            tag, "inlet_pressure_max", "pressure_drop_max"
        )
        if derived:
            updates["outlet_pressure_max"] = derived
            logger.debug(
                "Derived outlet_pressure_max = %s", derived
            )

    # Outlet Pressure (NOR) = Inlet Pressure (NOR) - Pressure Drop (NOR)
    if _is_empty_or_placeholder(tag.outlet_pressure_nor):
        derived = derive_outlet_pressure(
            tag, "inlet_pressure_nor", "pressure_drop_nor"
        )
        if derived:
            updates["outlet_pressure_nor"] = derived
            logger.debug(
                "Derived outlet_pressure_nor = %s", derived
            )

    # Outlet Pressure (MIN) = Inlet Pressure (MIN) - Pressure Drop (MIN)
    if _is_empty_or_placeholder(tag.outlet_pressure_min):
        derived = derive_outlet_pressure(
            tag, "inlet_pressure_min", "pressure_drop_min"
        )
        if derived:
            updates["outlet_pressure_min"] = derived
            logger.debug(
                "Derived outlet_pressure_min = %s", derived
            )

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
        postprocess_tag(
            t, use_engineered_defaults=use_engineered_defaults
        )
        for t in tags
    ]


# ---------------------------------------------------------------------------
# Three-phase pipeline entry points.
#
# These operate on PartialSV2ValveDatasheet (every field Optional) rather
# than the strict SV2ValveDatasheet, since a tag is only fully populated
# after all three phases run — see sv2_pipeline.run_three_phase_pipeline.
# ---------------------------------------------------------------------------

def apply_pre_llm_derivations(
    tag: PartialSV2ValveDatasheet,
    aux: dict,
) -> PartialSV2ValveDatasheet:
    """Phase 2a: deterministic derived fields computable from Phase-1
    direct fields and auxiliary raw values alone (no LLM-judgment fields
    needed yet). Runs in two stages since some fields (actuator colour,
    compressibility factor, specific heats ratio, specific gravity/MW)
    depend on other derived fields (actuator action, fluid phase) resolved
    in the first stage.
    """
    # --- Stage 1: fields independent of other derived fields ---
    updates: dict[str, object] = {}

    if tag.fluid_phase is None:
        derived = derive_fluid_phase(aux)
        if derived:
            updates["fluid_phase"] = derived

    if tag.actuator_action_air_failure is None:
        derived = derive_actuator_action_air_failure(aux)
        if derived:
            updates["actuator_action_air_failure"] = derived

    if tag.shut_off_pressure is None:
        derived = derive_shut_off_pressure(tag)
        if derived:
            updates["shut_off_pressure"] = derived

    if tag.seat_leakage_class is None:
        derived = derive_seat_leakage_class(aux)
        if derived:
            updates["seat_leakage_class"] = derived

    if tag.ndt_igc_test is None:
        derived = derive_ndt_igc_test(tag)
        if derived:
            updates["ndt_igc_test"] = derived

    if tag.cable_gland_certification is None:
        derived = derive_cable_gland_certification(tag, aux)
        if derived:
            updates["cable_gland_certification"] = derived

    if tag.peso_certificate is None:
        derived = derive_peso_certificate(aux)
        if derived is not None:
            updates["peso_certificate"] = derived

    for field_name, in_field, drop_field in (
        ("outlet_pressure_max", "inlet_pressure_max", "pressure_drop_max"),
        ("outlet_pressure_nor", "inlet_pressure_nor", "pressure_drop_nor"),
        ("outlet_pressure_min", "inlet_pressure_min", "pressure_drop_min"),
    ):
        if _is_empty_or_placeholder(getattr(tag, field_name, None)):
            derived = derive_outlet_pressure(tag, in_field, drop_field)
            if derived:
                updates[field_name] = derived

    for field_name, default_value in STANDARD_DERIVED_DEFAULTS.items():
        if getattr(tag, field_name, None) is None:
            updates[field_name] = default_value

    if updates:
        tag = tag.model_copy(update=updates)

    # --- Stage 2: fields depending on stage-1 outputs ---
    updates = {}

    if tag.actuator_colour is None:
        derived = derive_actuator_colour(tag)
        if derived:
            updates["actuator_colour"] = derived

    if _is_empty_or_placeholder(tag.compressibility_factor_z):
        derived = derive_compressibility_factor_z(tag)
        if derived:
            updates["compressibility_factor_z"] = derived

    if _is_empty_or_placeholder(tag.specific_heats_ratio_k):
        derived = derive_specific_heats_ratio_k(tag)
        if derived:
            updates["specific_heats_ratio_k"] = derived

    if tag.specific_gravity_or_molecular_weight is None:
        derived = derive_specific_gravity_or_molecular_weight(tag, aux)
        if derived:
            updates["specific_gravity_or_molecular_weight"] = derived

    if updates:
        tag = tag.model_copy(update=updates)

    return tag


def apply_post_llm_derivations(
    tag: PartialSV2ValveDatasheet,
    aux: dict,
) -> PartialSV2ValveDatasheet:
    """Phase 2b: deterministic derived fields that depend on the output of
    the Phase-2 LLM-judgment call. Currently just Airset Set Pressure /
    Filter (serial 56), which follows the LLM-derived Supply Pressure
    (serial 44).
    """
    if tag.airset_set_pressure_filter is not None:
        return tag
    derived = derive_airset_set_pressure_filter(tag)
    if derived:
        tag = tag.model_copy(
            update={"airset_set_pressure_filter": derived}
        )
    return tag


def apply_engineered_defaults(
    tag: PartialSV2ValveDatasheet,
    *,
    use_engineered_defaults: bool = True,
) -> PartialSV2ValveDatasheet:
    """Phase 3: fill the 5 "engineered" fields — never present in the
    enquiry PDF, sourced from organisational/vendor standards — from
    ENGINEERED_DEFAULTS. No-op when ``use_engineered_defaults`` is False.
    """
    if not use_engineered_defaults:
        return tag

    updates: dict[str, str] = {}
    for field_name, default_value in ENGINEERED_DEFAULTS.items():
        if getattr(tag, field_name, None) is not None:
            continue
        value = (
            default_value(tag)
            if callable(default_value)
            else default_value
        )
        updates[field_name] = value
        logger.debug(
            "Engineered default applied: %s = %s", field_name, value
        )

    if updates:
        tag = tag.model_copy(update=updates)
    return tag