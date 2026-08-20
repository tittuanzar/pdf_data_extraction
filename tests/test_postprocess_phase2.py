from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.pipeline import postprocess
from poc_valves.pydantic_output import PartialSV2ValveDatasheet


# Sample values below are taken from config/reference.yml's
# source.example_value / example_output columns for its three sample tags
# (Liquid / Steam / Gas service), which is the same ground-truth data
# data/Annexure D - TAG wise spec - Result.pdf documents.


class DeterministicDerivedFieldTests(unittest.TestCase):
    """Phase 2a/2b deterministic derive_* functions (no LLM)."""

    def test_derive_fluid_phase(self) -> None:
        self.assertEqual(
            postprocess.derive_fluid_phase(
                {"raw_upstream_condition": "Water"}
            ),
            "Liquid",
        )
        self.assertEqual(
            postprocess.derive_fluid_phase(
                {"raw_upstream_condition": "Steam"}
            ),
            "Steam",
        )
        self.assertEqual(
            postprocess.derive_fluid_phase(
                {"raw_upstream_condition": "Gas/Vapor"}
            ),
            "Gas",
        )
        self.assertIsNone(postprocess.derive_fluid_phase({}))

    def test_derive_specific_gravity_or_molecular_weight(self) -> None:
        liquid_tag = PartialSV2ValveDatasheet(fluid_phase="Liquid")
        self.assertEqual(
            postprocess.derive_specific_gravity_or_molecular_weight(
                liquid_tag, {"raw_inlet_density_or_mw": "955 kg/m3"}
            ),
            "SG = 0.955",
        )

        gas_tag = PartialSV2ValveDatasheet(fluid_phase="Gas")
        self.assertEqual(
            postprocess.derive_specific_gravity_or_molecular_weight(
                gas_tag, {"raw_inlet_density_or_mw": "28.96"}
            ),
            "28.96",
        )

        steam_tag = PartialSV2ValveDatasheet(fluid_phase="Steam")
        self.assertEqual(
            postprocess.derive_specific_gravity_or_molecular_weight(
                steam_tag, {"raw_inlet_density_or_mw": "n/a"}
            ),
            "N/A (steam)",
        )

    def test_derive_seat_leakage_class(self) -> None:
        self.assertEqual(
            postprocess.derive_seat_leakage_class(
                {"raw_tightness_requirements": "ANSI IV (standard)"}
            ),
            "IV (FCI 70.2)",
        )
        self.assertEqual(
            postprocess.derive_seat_leakage_class(
                {"raw_tightness_requirements": "ANSI V"}
            ),
            "V (FCI 70.2)",
        )
        self.assertEqual(
            postprocess.derive_seat_leakage_class(
                {"raw_tightness_requirements": "ANSI VI"}
            ),
            "VI (FCI 70.2)",
        )

    def test_derive_actuator_action_air_failure(self) -> None:
        self.assertEqual(
            postprocess.derive_actuator_action_air_failure(
                {"raw_power_failure_position": "Open"}
            ),
            "Air to Close (ATC)",
        )
        self.assertEqual(
            postprocess.derive_actuator_action_air_failure(
                {"raw_power_failure_position": "Close"}
            ),
            "Air to Open (ATO)",
        )

    def test_derive_shut_off_pressure(self) -> None:
        tag = PartialSV2ValveDatasheet(
            design_pressure_max="12 kgf/cm2-g"
        )
        self.assertEqual(
            postprocess.derive_shut_off_pressure(tag), "12 kgf/cm2-g"
        )

    def test_derive_cable_gland_certification_uses_aux_area_classification(
        self,
    ) -> None:
        tag = PartialSV2ValveDatasheet()
        result = postprocess.derive_cable_gland_certification(
            tag, {"raw_area_classification": "Zone 1 Gr. IIC T3"}
        )
        self.assertIsNotNone(result)
        self.assertIn("Zone 1", result)

    def test_derive_cable_gland_certification_positioner_cert_fallback_misses(
        self,
    ) -> None:
        # Regression check for the fixed bug: positioner_certification's
        # typical phrasing never contains "Zone", so the fallback alone
        # (no aux) should not produce a false match.
        tag = PartialSV2ValveDatasheet(
            positioner_certification="Intrinsic Safe Ex ia IIC, T6, WP IP66"
        )
        result = postprocess.derive_cable_gland_certification(tag, None)
        self.assertIsNone(result)

    def test_derive_ndt_igc_test_not_applicable_for_martensitic_trim(
        self,
    ) -> None:
        tag = PartialSV2ValveDatasheet(
            plug_material="410 (13Cr)",
            seat_material="St Gr6 (CoCrAlloy)",
        )
        self.assertEqual(
            postprocess.derive_ndt_igc_test(tag), "Not Applicable"
        )

    def test_derive_ndt_igc_test_applicable_for_austenitic_trim(
        self,
    ) -> None:
        tag = PartialSV2ValveDatasheet(
            plug_material="SS 316", seat_material="CF8M"
        )
        self.assertIn(
            "Applicable", postprocess.derive_ndt_igc_test(tag)
        )

    def test_derive_peso_certificate(self) -> None:
        self.assertTrue(
            postprocess.derive_peso_certificate(
                {"raw_area_classification": "Zone 1 Gr. IIC T3"}
            )
        )
        self.assertFalse(
            postprocess.derive_peso_certificate(
                {"raw_area_classification": "Non-hazardous"}
            )
        )

    def test_derive_outlet_pressure_matches_reference_example(self) -> None:
        tag = PartialSV2ValveDatasheet(
            inlet_pressure_max="6.8", pressure_drop_max="6.57"
        )
        result = postprocess.derive_outlet_pressure(
            tag, "inlet_pressure_max", "pressure_drop_max"
        )
        self.assertTrue(result.startswith("0.23"))


class ApplyPreLlmDerivationsTests(unittest.TestCase):
    """End-to-end Phase 2a orchestration for one sample tag."""

    def test_liquid_tag_chain(self) -> None:
        tag = PartialSV2ValveDatasheet(
            tag_no="1803-FV-10401",
            design_pressure_max="12 kgf/cm2-g",
            inlet_pressure_max="6.8",
            pressure_drop_max="6.57",
            inlet_pressure_nor="6.8",
            pressure_drop_nor="6.6",
            inlet_pressure_min="6.9",
            pressure_drop_min="6.7",
            plug_material="410 (13Cr)",
            seat_material="St Gr6 (CoCrAlloy)",
        )
        aux = {
            "raw_upstream_condition": "Water",
            "raw_power_failure_position": "Open",
            "raw_tightness_requirements": "ANSI IV (standard)",
            "raw_area_classification": "Zone 1 Gr. IIC T3",
            "raw_inlet_density_or_mw": "955 kg/m3",
        }

        result = postprocess.apply_pre_llm_derivations(tag, aux)

        self.assertEqual(result.fluid_phase, "Liquid")
        self.assertEqual(
            result.actuator_action_air_failure, "Air to Close (ATC)"
        )
        # actuator_colour depends on actuator_action_air_failure (stage 2)
        self.assertEqual(result.actuator_colour, "GREEN")
        self.assertEqual(result.shut_off_pressure, "12 kgf/cm2-g")
        self.assertEqual(result.seat_leakage_class, "IV (FCI 70.2)")
        self.assertTrue(result.outlet_pressure_max.startswith("0.23"))
        self.assertEqual(
            result.specific_gravity_or_molecular_weight, "SG = 0.955"
        )
        self.assertTrue(result.peso_certificate)
        # Standard derived defaults (always applied, ids 62/63/65)
        self.assertIsNotNone(result.ndt_rt_extent)
        self.assertIsNotNone(result.ndt_pmi_test)
        self.assertIsNotNone(result.valve_operating_signature)


class EngineeredDefaultsTests(unittest.TestCase):
    """Phase 3 — all 5 engineered fields, including the service-dependent
    'guiding' callable."""

    def test_all_five_engineered_fields_applied(self) -> None:
        tag = PartialSV2ValveDatasheet(fluid_phase="Liquid")
        result = postprocess.apply_engineered_defaults(
            tag, use_engineered_defaults=True
        )
        for field_name in (
            "painting_scheme",
            "cable_gland_type",
            "positioner_type",
            "bench_range",
            "guiding",
        ):
            self.assertIsNotNone(getattr(result, field_name), field_name)

    def test_guiding_is_service_dependent(self) -> None:
        steam_tag = PartialSV2ValveDatasheet(fluid_phase="Steam")
        liquid_tag = PartialSV2ValveDatasheet(fluid_phase="Liquid")

        steam_result = postprocess.apply_engineered_defaults(
            steam_tag, use_engineered_defaults=True
        )
        liquid_result = postprocess.apply_engineered_defaults(
            liquid_tag, use_engineered_defaults=True
        )

        self.assertEqual(steam_result.guiding, "Cage Guiding")
        self.assertEqual(liquid_result.guiding, "Heavy Top Guiding")

    def test_disabled_flag_is_noop(self) -> None:
        tag = PartialSV2ValveDatasheet()
        result = postprocess.apply_engineered_defaults(
            tag, use_engineered_defaults=False
        )
        self.assertIsNone(result.guiding)
        self.assertIsNone(result.painting_scheme)

    def test_does_not_overwrite_existing_value(self) -> None:
        tag = PartialSV2ValveDatasheet(cable_gland_type="Single compression")
        result = postprocess.apply_engineered_defaults(
            tag, use_engineered_defaults=True
        )
        self.assertEqual(result.cable_gland_type, "Single compression")


if __name__ == "__main__":
    unittest.main()
