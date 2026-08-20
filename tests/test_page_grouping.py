from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.pipeline.sv2_pipeline import (
    _group_pages_by_tag,
    _merge_page_group,
    _normalize_decimal_commas,
    _normalize_tag_no,
)


# Mirrors data/Annexure C - TAG wise spec.pdf's real structure: a cover
# page, then per tag a "main" datasheet page ("1 Tag No. ...") followed by
# a continuation page ("Tag Number: ...") carrying process notes/MDMT/IBR
# text several derived fields depend on.
COVER_PAGE = "ANNEXURE C\nTag wise Enquiry Data Sheet\n(File Format: PDF)"
MAIN_PAGE_1 = "1 Tag No. PID 1803-FV -10401\n2 Service SUSPECT CONDT PUMPS"
CONTINUATION_PAGE_1 = (
    "Tag Number:1803-FV -10401\nProcess Notes:\n"
    "2. Minimum Design Metal Temperature (MDMT): 4.4 C."
)
MAIN_PAGE_2 = "1 Tag No. PID 1803-FV -1104\n2 Service LP STEAM TO E-101"
CONTINUATION_PAGE_2 = (
    "Tag Number:1803-FV -1104\nProcess Notes:\n3. IBR Applicable."
)


class GroupPagesByTagTests(unittest.TestCase):
    def test_cover_page_is_dropped_and_pages_grouped_by_tag(self) -> None:
        pages = [
            {"page_number": 1, "text": COVER_PAGE, "tables": []},
            {"page_number": 2, "text": MAIN_PAGE_1, "tables": [{"a": 1}]},
            {
                "page_number": 3,
                "text": CONTINUATION_PAGE_1,
                "tables": [],
            },
            {"page_number": 4, "text": MAIN_PAGE_2, "tables": []},
            {
                "page_number": 5,
                "text": CONTINUATION_PAGE_2,
                "tables": [],
            },
        ]

        groups = _group_pages_by_tag(pages)

        self.assertEqual(len(groups), 2)
        self.assertEqual(
            [p["page_number"] for p in groups[0]], [2, 3]
        )
        self.assertEqual(
            [p["page_number"] for p in groups[1]], [4, 5]
        )

    def test_merge_page_group_concatenates_text_and_tables(self) -> None:
        group = [
            {
                "page_number": 2,
                "text": MAIN_PAGE_1,
                "tables": [{"a": 1}],
                "image_b64": "abc",
            },
            {
                "page_number": 3,
                "text": CONTINUATION_PAGE_1,
                "tables": [{"b": 2}],
            },
        ]

        merged = _merge_page_group(group)

        self.assertEqual(merged["page_number"], 2)
        self.assertIn("1 Tag No.", merged["text"])
        self.assertIn("MDMT", merged["text"])
        self.assertEqual(merged["tables"], [{"a": 1}, {"b": 2}])
        self.assertEqual(merged["image_b64"], "abc")

    def test_continuation_page_without_preceding_main_is_kept(
        self,
    ) -> None:
        pages = [
            {
                "page_number": 1,
                "text": CONTINUATION_PAGE_1,
                "tables": [],
            }
        ]
        groups = _group_pages_by_tag(pages)
        self.assertEqual(len(groups), 1)

    def test_page_with_no_tag_markers_is_dropped(self) -> None:
        pages = [{"page_number": 1, "text": COVER_PAGE, "tables": []}]
        self.assertEqual(_group_pages_by_tag(pages), [])


class NormalizeTagNoTests(unittest.TestCase):
    def test_strips_leading_pid_token_and_variants(self) -> None:
        self.assertEqual(
            _normalize_tag_no("PID 1803-FV -10401"), "1803-FV -10401"
        )
        self.assertEqual(
            _normalize_tag_no("PID: 1803-FV-10401"), "1803-FV-10401"
        )
        self.assertEqual(
            _normalize_tag_no("PID-1803-FV-10401"), "1803-FV-10401"
        )
        self.assertEqual(
            _normalize_tag_no("pid 1803-FV-1104"), "1803-FV-1104"
        )

    def test_leaves_value_without_pid_prefix_unchanged(self) -> None:
        self.assertEqual(
            _normalize_tag_no("1803-FV-10401"), "1803-FV-10401"
        )

    def test_does_not_strip_pid_appearing_mid_value(self) -> None:
        # "PID" only stripped when it's the leading token, not when it
        # appears elsewhere (e.g. a P&ID document reference number).
        value = "2047-1803-EPR-PID-0104-01"
        self.assertEqual(_normalize_tag_no(value), value)

    def test_handles_none_and_empty(self) -> None:
        self.assertIsNone(_normalize_tag_no(None))
        self.assertEqual(_normalize_tag_no(""), "")


class NormalizeDecimalCommasTests(unittest.TestCase):
    def test_replaces_comma_between_digits(self) -> None:
        self.assertEqual(_normalize_decimal_commas("10,5"), "10.5")
        self.assertEqual(_normalize_decimal_commas("6,8"), "6.8")
        self.assertEqual(_normalize_decimal_commas("0,276"), "0.276")

    def test_handles_units_and_surrounding_text(self) -> None:
        self.assertEqual(
            _normalize_decimal_commas("6,8 kgf/cm2-g"), "6.8 kgf/cm2-g"
        )
        self.assertEqual(
            _normalize_decimal_commas("10,5 Am3/h"), "10.5 Am3/h"
        )

    def test_leaves_value_without_comma_unchanged(self) -> None:
        self.assertEqual(_normalize_decimal_commas("6.8"), "6.8")
        self.assertEqual(
            _normalize_decimal_commas("Single Seated Globe"),
            "Single Seated Globe",
        )

    def test_leaves_comma_not_between_digits_unchanged(self) -> None:
        # e.g. a comma used as a list/word separator rather than decimal.
        self.assertEqual(
            _normalize_decimal_commas("Open, Close"), "Open, Close"
        )

    def test_handles_none_and_empty(self) -> None:
        self.assertIsNone(_normalize_decimal_commas(None))
        self.assertEqual(_normalize_decimal_commas(""), "")


if __name__ == "__main__":
    unittest.main()
