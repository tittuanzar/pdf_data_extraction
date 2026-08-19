from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.field_mapping_register.models import PageText
from poc_valves.field_mapping_register.runner import (
    infer_tag_ids,
    merge_rows,
    normalize_rows,
)


class FieldMappingRegisterTests(unittest.TestCase):
    """Tests for field mapping register operations."""

    def test_infer_tag_ids(self) -> None:
        """Test tag ID inference from page texts."""
        page_texts = [
            PageText(
                page_number=1,
                text=(
                    "1803-FV-10401 | 1803-FV-1104 | "
                    "1803-FV-1403\n"
                    "serial_number | enquiry_field_name"
                ),
            )
        ]
        self.assertEqual(
            infer_tag_ids(page_texts),
            ["1803-FV-10401", "1803-FV-1104", "1803-FV-1403"],
        )

    def test_merge_rows_prefers_existing_and_new_values(
        self,
    ) -> None:
        """Test row merging prefers longer values."""
        merged, warnings = merge_rows(
            [
                [
                    {
                        "serial_number": "1",
                        "enquiry_field_name": "Field A",
                        "source_pages": ["1"],
                    }
                ],
                [
                    {
                        "serial_number": "1",
                        "sv2_field_value": "X",
                        "source_pages": ["2"],
                    }
                ],
            ]
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            merged[0]["sv2_field_value"], "X"
        )
        self.assertEqual(
            merged[0]["source_pages"], ["1", "2"]
        )

    def test_normalize_rows_splits_pipe_values(self) -> None:
        """Test row normalization splits pipe-separated values."""
        rows, review_items, warnings = normalize_rows(
            [
                {
                    "serial_number": "1",
                    "enquiry_field_value": "6.8 | 3.31 | 4.3",
                    "sv2_field_value": "A | B | C",
                    "mapping_logic": "copy",
                }
            ],
            ["TAG1", "TAG2", "TAG3"],
        )
        self.assertEqual(warnings, [])
        self.assertEqual(
            rows[0]["enquiry_field_value_TAG1"], "6.8"
        )
        self.assertEqual(
            rows[0]["enquiry_field_value_TAG2"], "3.31"
        )
        self.assertEqual(
            rows[0]["sv2_field_value_TAG3"], "C"
        )
        self.assertEqual(review_items, [])


if __name__ == "__main__":
    unittest.main()