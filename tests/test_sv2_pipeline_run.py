from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.core.config import settings
from poc_valves.core.llm.extractor import DerivedJudgmentFields
from poc_valves.core.pipeline.sv2_pipeline import SV2Pipeline

# Successful Phase 2 calls set this so failure-isolation tests can tell a
# tag that went through Phase 2 apart from one whose Phase 2 call raised.
_JUDGMENT_FLOW_CHARACTERISTIC = "EQUAL_PERCENTAGE"


class FakeLLM:
    """Duck-typed stand-in for LLMClient — no real OpenAI calls."""

    def __init__(self) -> None:
        self.client = object()  # accessed eagerly by SV2Pipeline.run

    def format_cost(self, cost_usd: float) -> str:
        return f"${cost_usd:.4f}"


class FakeExtractor:
    """Duck-typed stand-in for SV2FieldExtractor with no real LLM calls.

    ``extract_page`` sleeps longer for lower page numbers, so later tag
    groups resolve first under concurrency — this lets tests prove
    ``SV2Pipeline.run`` restores original page order regardless of
    completion order.
    """

    def __init__(self, *, fail_phase2_for_page: int | None = None) -> None:
        self.llm = FakeLLM()
        self._fail_phase2_for_page = fail_phase2_for_page

    def extract_page(self, page_text, page_number, *, image_b64="", tables=None, guide=None):
        time.sleep(0.01 * (10 - page_number))
        direct_values = {"tag_no": f"TAG-{page_number}"}
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return direct_values, {}, usage, 0.01

    def derive_judgment_fields(self, tag, aux):
        if self._fail_phase2_for_page is not None and tag.tag_no == f"TAG-{self._fail_phase2_for_page}":
            raise RuntimeError("simulated Phase 2 failure")
        usage = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        return (
            DerivedJudgmentFields(flow_characteristic=_JUDGMENT_FLOW_CHARACTERISTIC),
            usage,
            0.002,
        )


def _pages(n: int) -> list[dict]:
    return [
        {"page_number": i, "text": f"1 Tag No. TAG-{i}\nService X", "tables": []}
        for i in range(1, n + 1)
    ]


class Sv2PipelineRunTests(unittest.TestCase):
    def test_order_preserved_under_concurrency(self) -> None:
        pipeline = SV2Pipeline(FakeExtractor(), use_engineered_defaults=False)
        pages = _pages(5)

        result = pipeline.run(pages)

        self.assertEqual(
            [tag.tag_no for tag in result.tags],
            [f"TAG-{i}" for i in range(1, 6)],
        )

    def test_usage_and_cost_are_aggregated_across_tags(self) -> None:
        pipeline = SV2Pipeline(FakeExtractor(), use_engineered_defaults=False)
        pages = _pages(3)

        logger_name = "poc_valves.core.pipeline.sv2_pipeline"
        with self.assertLogs(logger_name, level="INFO") as cm:
            pipeline.run(pages)

        totals = [r for r in cm.records if "TOTAL usage" in r.getMessage()]
        self.assertEqual(len(totals), 1)
        _, _, prompt_tokens, completion_tokens, total_tokens, cost_str = totals[0].args

        self.assertEqual(prompt_tokens, 3 * (10 + 2))
        self.assertEqual(completion_tokens, 3 * (5 + 1))
        self.assertEqual(total_tokens, 3 * (15 + 3))
        self.assertEqual(cost_str, "$0.0360")  # 3 * (0.01 + 0.002)

    def test_phase2_failure_is_isolated_to_its_own_tag(self) -> None:
        pipeline = SV2Pipeline(
            FakeExtractor(fail_phase2_for_page=2), use_engineered_defaults=False
        )
        pages = _pages(3)

        result = pipeline.run(pages)

        self.assertEqual(len(result.tags), 3)
        by_tag = {tag.tag_no: tag for tag in result.tags}
        # Tag 2's Phase 2 call raised, so it never got judgment updates and
        # this required-but-judgment-only field falls back to the
        # _finalize_tag placeholder.
        self.assertEqual(by_tag["TAG-2"].flow_characteristic, "NOT EXTRACTED")
        # The other tags' Phase 2 calls succeeded normally.
        self.assertEqual(
            by_tag["TAG-1"].flow_characteristic, _JUDGMENT_FLOW_CHARACTERISTIC
        )
        self.assertEqual(
            by_tag["TAG-3"].flow_characteristic, _JUDGMENT_FLOW_CHARACTERISTIC
        )

    def test_concurrency_knob_is_honored(self) -> None:
        with mock.patch.object(
            type(settings), "max_concurrency", property(lambda self: 1)
        ):
            pipeline = SV2Pipeline(FakeExtractor(), use_engineered_defaults=False)
            result = pipeline.run(_pages(3))

        self.assertEqual(
            [tag.tag_no for tag in result.tags], ["TAG-1", "TAG-2", "TAG-3"]
        )


if __name__ == "__main__":
    unittest.main()
