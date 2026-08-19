"""
Pydantic model for the SV2 Control Valve Datasheet.

Generated from the customer-enquiry -> SV2 field mapping table.
Field order follows the "Serial Number" column of the source table.
Each field's description is built from that row's Enquiry Field,
Mapping Category (Direct/Derived), Field Requirement (Mandatory/
Optional/Conditional), Logic, Value, and Notes columns.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SV2ValveDatasheet(BaseModel):
    """SV2 Control Valve Datasheet model."""

    # 1. Tag No
    tag_no: str = Field(
        ...,
        description=(
            "Valve tag number, copied directly from the customer "
            "enquiry (Row 1). Format example: 'FV-XXXX'. Mandatory. "
            "Must match the tag numbering convention used across "
            "the enquiry."
        ),
    )

    # 2. Service
    service: str = Field(
        ...,
        description=(
            "Service/application description (e.g. Air, Preheat, "
            "Aeration), copied directly from the enquiry's Service "
            "Description field. Mandatory. May include equipment "
            "name in parentheses; carried forward to help determine "
            "fluid name/phase downstream."
        ),
    )

    # 3. Fluid Name
    fluid_name: str = Field(
        ...,
        description=(
            "Name of the process fluid (e.g. Plant Air, Suspect "
            "Liquid, Water, Steam, Propylene), copied directly "
            "from the enquiry's fluid row. Mandatory; used to "
            "determine fluid properties and upstream conditions."
        ),
    )

    # 4. Fluid Phase
    fluid_phase: str = Field(
        ...,
        description=(
            "Physical state of the fluid at valve conditions: "
            "Liquid, Vapor/Gas, or Steam/HP. Derived (mapped from "
            "the enquiry's Water/Liquid/Steam state row). "
            "Mandatory; determines which sizing equation path "
            "(liquid vs. gas/vapor) is used downstream."
        ),
    )

    # 5. Inlet Pipe Size/Sch
    inlet_pipe_size_sch: str = Field(
        ...,
        description=(
            "Inlet line size and schedule (e.g. '3 in / STD'), "
            "extracted from the enquiry's line/piping column. "
            "Direct mapping, Mandatory. Reflects the upstream pipe "
            "the valve connects to."
        ),
    )

    # 6. Outlet Pipe Size/Sch
    outlet_pipe_size_sch: str = Field(
        ...,
        description=(
            "Outlet line size and schedule (e.g. '3 in / STD'), "
            "extracted from the enquiry's line/piping column "
            "(same source row as inlet). Direct mapping, Mandatory."
        ),
    )

    # 7. Design Pressure (MAX)
    design_pressure_max: float = Field(
        ...,
        description=(
            "Maximum design pressure in kgf/cm2(g), taken "
            "directly from the enquiry Design Pressure field. "
            "Mandatory. Drives the pressure class (150#, 300#, "
            "600#, etc.) selection."
        ),
    )

    # 8. Design Pressure (MIN)
    design_pressure_min: Optional[float] = Field(
        None,
        description=(
            "Minimum design pressure in kgf/cm2(g). Direct "
            "mapping, Mandatory where applicable (e.g. Full "
            "Vacuum case). Used with MAX to bound the pressure "
            "class. Extract maximum design temperature from "
            "Row 27 of table."
        ),
    )

    # 9. Design Temp (MAX)
    design_temp_max: float = Field(
        ...,
        description=(
            "Maximum design temperature in degrees C, copied "
            "directly from the enquiry Design Temperature field. "
            "Mandatory; used for material grade selection "
            "(e.g. grades above 230 degC)."
        ),
    )

    # 10. Design Temp (MIN) / MDMT
    design_temp_min_mdmt: float = Field(
        ...,
        description=(
            "Minimum design metal temperature (MDMT), often "
            "taken from the ambient temperature text on the "
            "enquiry (e.g. 4.4 degC). Derived, Mandatory. "
            "Impacts low-temperature material grade requirements."
        ),
    )

    # 11. Flow Rate (MAX)
    flow_rate_max: float = Field(
        ...,
        description=(
            "Maximum flow rate (units per enquiry, e.g. m3/h, "
            "Nm3/h, or kg/h), extracted from the enquiry's flow "
            "column header row. Direct mapping, Mandatory; the "
            "design/rating case for sizing."
        ),
    )

    # 12. Flow Rate (NOR)
    flow_rate_nor: float = Field(
        ...,
        description=(
            "Normal flow rate, same source/units as Flow Rate "
            "(MAX). Direct mapping, Mandatory; used as the "
            "normal-operating design target and to check "
            "gauge/valve travel behavior."
        ),
    )

    # 13. Flow Rate (MIN)
    flow_rate_min: float = Field(
        ...,
        description=(
            "Minimum flow rate, same source/units as Flow Rate "
            "(MAX). Direct mapping, Mandatory; used to check "
            "minimum controllable throttle position/turndown."
        ),
    )

    # 14. Inlet Pressure (MAX)
    inlet_pressure_max: float = Field(
        ...,
        description=(
            "Maximum inlet (upstream) pressure in kgf/cm2(g), "
            "extracted directly from the enquiry's pressure row. "
            "Mandatory; converted to gauge units as needed."
        ),
    )

    # 15. Inlet Pressure (NOR)
    inlet_pressure_nor: float = Field(
        ...,
        description=(
            "Normal inlet pressure in kgf/cm2(g), same source "
            "row as Inlet Pressure (MAX). Direct mapping, "
            "Mandatory."
        ),
    )

    # 16. Inlet Pressure (MIN)
    inlet_pressure_min: float = Field(
        ...,
        description=(
            "Minimum inlet pressure in kgf/cm2(g), same source "
            "row as Inlet Pressure (MAX). Direct mapping, "
            "Mandatory."
        ),
    )

    # 17. Outlet Pressure (MAX)
    outlet_pressure_max: Optional[float] = Field(
        None,
        description=(
            "Maximum outlet (downstream) pressure in "
            "kgf/cm2(g). Derived when not directly stated - "
            "back-calculated as Inlet Pressure (MAX) minus "
            "Pressure Drop (MAX)."
        ),
    )

    # 18. Outlet Pressure (NOR)
    outlet_pressure_nor: Optional[float] = Field(
        None,
        description=(
            "Normal outlet pressure in kgf/cm2(g). Derived "
            "when not directly stated - back-calculated as "
            "Inlet Pressure (NOR) minus Pressure Drop (NOR)."
        ),
    )

    # 19. Outlet Pressure (MIN)
    outlet_pressure_min: Optional[float] = Field(
        None,
        description=(
            "Minimum outlet pressure in kgf/cm2(g). Derived "
            "when not directly stated - back-calculated as "
            "Inlet Pressure (MIN) minus Pressure Drop (MIN)."
        ),
    )

    # 20. Pressure Drop (MAX)
    pressure_drop_max: float = Field(
        ...,
        description=(
            "Maximum pressure drop across the valve in "
            "kgf/cm2 (dP), copied directly from the enquiry. "
            "Mandatory; may not represent the actual sizing "
            "pressure drop but bounds it."
        ),
    )

    # 21. Pressure Drop (NOR)
    pressure_drop_nor: float = Field(
        ...,
        description=(
            "Normal pressure drop across the valve in kgf/cm2, "
            "direct mapping from the enquiry. Mandatory; "
            "primary Cv sizing basis."
        ),
    )

    # 22. Pressure Drop (MIN)
    pressure_drop_min: float = Field(
        ...,
        description=(
            "Minimum pressure drop across the valve in "
            "kgf/cm2, direct mapping from the enquiry. "
            "Mandatory; used to check low-dP operating cases."
        ),
    )

    # 23. Temperature (MAX)
    temperature_max: float = Field(
        ...,
        description=(
            "Maximum operating (inlet) fluid temperature in "
            "degrees C, extracted directly from the enquiry. "
            "Mandatory; high temperature affects bonnet type "
            "and bolting selection."
        ),
    )

    # 24. Temperature (NOR)
    temperature_nor: float = Field(
        ...,
        description=(
            "Normal operating fluid temperature in degrees C, "
            "same source row as Temperature (MAX). Direct "
            "mapping, Mandatory."
        ),
    )

    # 25. Temperature (MIN)
    temperature_min: float = Field(
        ...,
        description=(
            "Minimum operating fluid temperature in degrees C, "
            "same source row as Temperature (MAX). Direct "
            "mapping, Mandatory."
        ),
    )

    # 26. Specific Gravity / Molecular Weight
    specific_gravity_or_molecular_weight: float = Field(
        ...,
        description=(
            "Specific gravity (liquid, relative to water) or "
            "molecular weight in kg/kmol (gas, relative to air), "
            "read from the enquiry's density/MW table depending "
            "on fluid phase. Derived; for liquids, spec. gravity "
            "affects the Fp/Fl sizing factors."
        ),
    )

    # 27. Viscosity
    viscosity: float = Field(
        ...,
        description=(
            "Fluid viscosity, extracted from the enquiry (only "
            "for liquid service; leave default/NA for gas). "
            "Direct mapping; viscosity affects the "
            "Reynolds-number correction factor Fp."
        ),
    )

    # 28. Compressibility Factor (Z)
    compressibility_factor_z: Optional[float] = Field(
        None,
        description=(
            "Gas compressibility factor Z (dimensionless, "
            "liquid = N/A), extracted from the enquiry's "
            "steam/gas table. Direct mapping, Conditional "
            "(gas service only). For ideal gases Z = 1; it "
            "affects the choked-flow ratio calculation, so a "
            "conservative value of 1.4 or per steam table is "
            "used if unknown."
        ),
    )

    # 29. Specific Heats Ratio (K)
    specific_heats_ratio_k: Optional[float] = Field(
        None,
        description=(
            "Ratio of specific heats, gamma (liquid = N/A), "
            "extracted from the enquiry's gas table (e.g. "
            "Isentropic Air = 1.4, CO2 = 1.3). Direct mapping, "
            "Conditional (gas service only); must be in "
            "absolute terms."
        ),
    )

    # 30. Vapour Pressure
    vapour_pressure: Optional[float] = Field(
        None,
        description=(
            "Fluid vapour pressure in kgf/cm2(a), extracted "
            "from the enquiry's flashing/liquid service data. "
            "Conditional (liquid service that may flash/ "
            "cavitate); vapour pressure gauge value in "
            "absolute terms is used to determine choked flow / "
            "cavitation onset."
        ),
    )

    # 31. Valve Type
    valve_type: str = Field(
        ...,
        description=(
            "Body style of the valve (e.g. Single Seated "
            "Globe), copied verbatim from the enquiry's Body "
            "Type field. Mandatory."
        ),
    )

    # 32. Body Material
    body_material: str = Field(
        ...,
        description=(
            "Valve body material of construction (e.g. ASTM "
            "A216 WCB, A351 CF8M), copied verbatim from the "
            "enquiry's Body Material grade/customer standard "
            "row 36. Mandatory."
        ),
    )

    # 33. Bonnet Type
    bonnet_type: str = Field(
        ...,
        description=(
            "Bonnet construction/extension style (e.g. Bolted, "
            "Bonnet Extension for cold/hot service), copied "
            "directly from the enquiry. Mandatory; flow "
            "direction affects sizing and stability at "
            "temperature extremes above/below thresholds."
        ),
    )

    # 34. Flow Direction
    flow_direction: str = Field(
        ...,
        description=(
            "Direction of flow through the valve (e.g. "
            "Flow-to-Open / Flow-to-Close), copied directly "
            "from the enquiry. Mandatory; determines whether "
            "the seat closes with or against flow."
        ),
    )

    # 35. End Connection & Rating
    end_connection_rating: str = Field(
        ...,
        description=(
            "End connection type and pressure class/rating "
            "(e.g. Flanged, ANSI Class 150/300 RF, Butt Weld), "
            "copied directly from the enquiry's connection row. "
            "Mandatory; equal-percentage vs. linear "
            "characteristic and body rating vary with class."
        ),
    )

    # 36. Flow Characteristic
    flow_characteristic: str = Field(
        ...,
        description=(
            "Inherent flow-vs-travel characteristic (e.g. "
            "Linear, Equal Percentage), derived from the "
            "enquiry's opening/drop ratio data. Derived, "
            "Mandatory; chosen based on how flow approaches or "
            "exceeds the sizing constant."
        ),
    )

    # 37. Trim / Plug Type
    trim_plug_type: str = Field(
        ...,
        description=(
            "Trim/plug design (e.g. Low Noise, Anti-Cavitation "
            "Contoured), copied directly from the enquiry's "
            "trim description. Mandatory; selected based on "
            "expected noise/cavitation outcome from Cv "
            "calculations."
        ),
    )

    # 38. Plug Material
    plug_material: str = Field(
        ...,
        description=(
            "Plug/ball material and hardfacing (e.g. 410 SS + "
            "Stellite overlay, or hardened SS 316), copied "
            "verbatim from the enquiry. Mandatory."
        ),
    )

    # 39. Seat Material
    seat_material: str = Field(
        ...,
        description=(
            "Seat material and hardfacing (e.g. St. Gr. 6 / "
            "CoCr, or SS 316 + Stellite overlay), mapped from "
            "the enquiry's seat/seal material and leakage "
            "class columns. Direct mapping; Class V required "
            "for tighter leakage per the captured leakage "
            "requirements."
        ),
    )

    # 40. Seat Leakage Class
    seat_leakage_class: str = Field(
        ...,
        description=(
            "Seat leakage tightness class per ANSI/FCI 70.2 "
            "(e.g. Class IV, Class V), derived from the "
            "enquiry's leakage/vendor arrangement data. "
            "Mandatory."
        ),
    )

    # 41. Guiding
    guiding: Optional[str] = Field(
        None,
        description=(
            "Trim guiding arrangement (e.g. Top Guide, "
            "Top-and-Bottom Guided), derived from the enquiry "
            "(vendor-standard where not explicitly stated). "
            "Optional/Engineer's choice; all three guiding "
            "types cover most common pneumatic valves and are "
            "used for high-pressure-drop service."
        ),
    )

    # 42. Actuator Type
    actuator_type: str = Field(
        ...,
        description=(
            "Actuator type and design (e.g. Pneumatic "
            "Spring-Diaphragm, Pneumatic Yoke/Cylinder), "
            "copied directly from the enquiry's actuator row. "
            "Mandatory; the actuator type for high-load valves."
        ),
    )

    # 43. Actuator Action / Air Failure
    actuator_action_air_failure: str = Field(
        ...,
        description=(
            "Fail-safe action on loss of air/power (e.g. Fail "
            "Open (FO), Fail Close (FC), Air-to-Open, "
            "Air-to-Close), derived by checking both the "
            "enquiry's Air Failure and Action columns. "
            "Mandatory; colour coding (e.g. GREEN = fail open "
            "actuator) should be checked against the mill "
            "instruction/spec."
        ),
    )

    # 44. Supply Pressure
    supply_pressure: float = Field(
        ...,
        description=(
            "Available instrument air/supply pressure in "
            "kgf/cm2, converted from the enquiry's stated "
            "min/max range (e.g. 3.5-5.0 kgf/cm2). Derived, "
            "Mandatory; used to size the actuator so it can "
            "overhaul the required stroke and travel plus "
            "margin, as determined by the engineer."
        ),
    )

    # 45. Bench Range
    bench_range: str = Field(
        ...,
        description=(
            "Actuator spring bench-set range in psi "
            "(e.g. 3-15 psi), chosen by the vendor based on "
            "the shut-off pressure calculation. "
            "Engineer/Mandatory; used to size the actuator and "
            "ensure adequate shut-off as determined by the "
            "engineer."
        ),
    )

    # 46. Shut-off Pressure
    shut_off_pressure: Optional[float] = Field(
        None,
        description=(
            "Differential pressure the actuator must shut off "
            "against, in kgf/cm2, derived from this value "
            "where not stated directly. Mandatory; "
            "tag/loop-specific (e.g. Air Failure) requirement "
            "flowing from the shut-off calc; drives the "
            "positioner/handwheel requirement when unavailable."
        ),
    )

    # 47. Handwheel
    handwheel: Optional[str] = Field(
        None,
        description=(
            "Handwheel mounting requirement (e.g. "
            "Top-mounted, None), copied from the enquiry where "
            "stated. Direct mapping, Optional; NA if not "
            "required, i.e. Yes/No/mounted position."
        ),
    )

    # 48. Positioner Type
    positioner_type: Optional[str] = Field(
        None,
        description=(
            "Positioner type/selection (e.g. Smart "
            "Single-Acting, Electro-Pneumatic), derived per "
            "vendor selection where the enquiry doesn't state "
            "one. Engineer/Mandatory; a smart positioner "
            "eliminates the need for separate hardware."
        ),
    )

    # 49. Positioner Make
    positioner_make: Optional[str] = Field(
        None,
        description=(
            "Positioner manufacturer/brand (e.g. Metso, "
            "Siemens), derived per customer/vendor preference "
            "(e.g. 'as per customer' -> Metso as KSI "
            "standard). Mandatory."
        ),
    )

    # 50. Positioner Model
    positioner_model: Optional[str] = Field(
        None,
        description=(
            "Specific positioner model number (e.g. Metso "
            "ND9103-HX-T or ND9103-HX-T:H:P with position "
            "transmitter), derived per vendor's certified "
            "model list. Mandatory."
        ),
    )

    # 51. Positioner Protocol / Casing
    positioner_protocol_casing: Optional[str] = Field(
        None,
        description=(
            "Positioner communication protocol and housing "
            "(e.g. HART/4-20mA, Profibus, casing material), "
            "combined from the enquiry's signal field "
            "('Close' = 'Reverse'). Mandatory."
        ),
    )

    # 52. Positioner Action / Input Signal
    positioner_action_input_signal: Optional[str] = Field(
        None,
        description=(
            "Positioner action (Direct/Reverse) and input "
            "signal (e.g. 4-20mA), derived from the enquiry's "
            "action/signal tags. Mandatory; increasing signal "
            "increases valve opening typically for FTO valves - "
            "increase closes for FTC."
        ),
    )

    # 53. Positioner Certification
    positioner_certification: Optional[str] = Field(
        None,
        description=(
            "Hazardous-area certification for the positioner "
            "(e.g. ATEX/IECEx Zone 1, Gr. IIC, Intrinsically "
            "Safe, IP66/67), copied from the enquiry's area "
            "classification. Mandatory; zone requirement is "
            "intrinsic - flameproof also acceptable unless "
            "proof is required per positioning/quality spec."
        ),
    )

    # 54. Position Transmitter
    position_transmitter: Optional[str] = Field(
        None,
        description=(
            "Whether a position transmitter (4-20mA, "
            "LVDT/Hall-effect) is supplied, conditional on "
            "VDC/analog output requirement. Conditional; "
            "required if non-integral, separate air-set "
            "conditions instructed before regulation."
        ),
    )

    # 55. Airset Make/Model/Qty
    airset_make_model_qty: Optional[str] = Field(
        None,
        description=(
            "Air filter-regulator (airset) make, model and "
            "quantity (e.g. Fisher/Shafto/SMC, watts), derived "
            "from the enquiry's airset gauge/set-pressure row. "
            "Mandatory."
        ),
    )

    # 56. Airset Set Pressure / Filter
    airset_set_pressure_filter: Optional[str] = Field(
        None,
        description=(
            "Airset set pressure and filter rating (e.g. "
            "5-micron filter element, gauge/micron rating), "
            "derived by vendor from the enquiry's supply spec. "
            "Mandatory."
        ),
    )

    # 57. Painting Scheme
    painting_scheme: Optional[str] = Field(
        None,
        description=(
            "Painting/coating scheme (e.g. per PO or FRAL "
            "7001, primer + top coat), engineer's default if "
            "not stated in the enquiry. Optional/Engineer; "
            "painting scheme varies with the PO's painting "
            "specification (e.g. RAL/228HS colour code)."
        ),
    )

    # 58. Actuator Colour
    actuator_colour: Optional[str] = Field(
        None,
        description=(
            "Actuator housing colour, often coded to fail "
            "action (e.g. RED = Fail Close, GREEN = Fail Open "
            "per KSI standard), engineer/mandatory per "
            "certification requirements."
        ),
    )

    # 59. Cable Gland Type
    cable_gland_type: Optional[str] = Field(
        None,
        description=(
            "Cable gland type (e.g. compression, double "
            "compression), not stated by default in enquiry - "
            "engineer/vendor standard applies. Optional."
        ),
    )

    # 60. Cable Gland Certification
    cable_gland_certification: Optional[str] = Field(
        None,
        description=(
            "Hazardous-area certification for the cable gland "
            "(e.g. ATEX/IECEx Zone 1 Gr. IIC, Ex d/e), "
            "copied from the enquiry's area classification if "
            "fluid/spec requires. Conditional; cable gland "
            "certificate must match the process/IBR "
            "certificate requirement."
        ),
    )

    # 61. IBR Applicability
    ibr_applicability: Optional[bool] = Field(
        None,
        description=(
            "Whether Indian Boiler Regulations (IBR) "
            "certification applies, derived from the process "
            "fluid/steam service (Boiler Form IIIC). "
            "Conditional; when applicable, IBR certificate is "
            "required during manufacturing and can add several "
            "weeks to the delivery schedule."
        ),
    )

    # 62. NDT - RT Extent
    ndt_rt_extent: Optional[str] = Field(
        None,
        description=(
            "Radiographic testing (RT) extent required "
            "(e.g. 10% RT, spot RT per body/bonnet), default "
            "per standard unless the enquiry states otherwise. "
            "Optional; special testing/quality requirement."
        ),
    )

    # 63. NDT - PMI Test
    ndt_pmi_test: Optional[str] = Field(
        None,
        description=(
            "Positive Material Identification (PMI) test "
            "requirement, identified per standard grade for "
            "trim/body parts. Optional; PMI verifies that trim "
            "material is the correct/specified grade."
        ),
    )

    # 64. NDT - IGC Test
    ndt_igc_test: Optional[str] = Field(
        None,
        description=(
            "Intergranular Corrosion (IGC) test requirement, "
            "triggered when an austenitic stainless-steel "
            "trim/seat/plug is populated (e.g. per ASTM A262 "
            "practice E). Conditional; verifies resistance to "
            "sensitization for stainless materials."
        ),
    )

    # 65. Valve Operating Signature
    valve_operating_signature: Optional[str] = Field(
        None,
        description=(
            "Requirement for a recorded valve operating (loop "
            "test) signature, not standard by default. "
            "Optional; special testing/quality requirement for "
            "critical service valves."
        ),
    )

    # 66. PESO Certificate
    peso_certificate: Optional[bool] = Field(
        None,
        description=(
            "Whether a PESO (Petroleum and Explosives Safety "
            "Organisation) certificate is required, derived "
            "from the hazardous-area classification "
            "(Zone 1 Gr. IIC). Conditional; PESO certificate "
            "must be added and can add roughly 4-6 weeks to "
            "the delivery schedule."
        ),
    )

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "title": "SV2 Control Valve Datasheet",
            "description": (
                "Fields mirror the customer-enquiry -> SV2 "
                "mapping table: Serial 1 (Tag No) through "
                "Serial 66 (PESO Certificate)."
            ),
        },
    )


class ParsedOutput(BaseModel):
    """Parsed output containing multiple valve datasheets."""

    tags: list[SV2ValveDatasheet]


if __name__ == "__main__":
    # Quick sanity check: print the generated JSON schema
    import json

    print(
        json.dumps(
            SV2ValveDatasheet.model_json_schema(), indent=2
        )[:2000]
    )