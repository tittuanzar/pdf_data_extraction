from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.pdf.pdf_text import PdfPageText
from poc_valves.pdf.tagged_products import build_tagged_product_sections, extract_tag_ids


class TaggedProductsTests(unittest.TestCase):
    def test_extract_tag_ids(self) -> None:
        text = "1803-FV-10401 | 1803-FV-1104 | 1803-FV-1403"
        self.assertEqual(
            extract_tag_ids(text),
            ["1803-FV-10401", "1803-FV-1104", "1803-FV-1403"],
        )

    def test_build_sections_close_on_process_notes(self) -> None:
        pages = [
            PdfPageText(page_number=1, text="1803-FV-10401\nSome product details"),
            PdfPageText(page_number=2, text="More details\nProcess Notes\nEnd"),
            PdfPageText(page_number=3, text="1803-FV-1104\nSecond product"),
        ]
        sections = build_tagged_product_sections(pages)
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].tag_id, "1803-FV-10401")
        self.assertEqual(sections[0].end_page, 2)
        self.assertEqual(sections[1].tag_id, "1803-FV-1104")


if __name__ == "__main__":
    unittest.main()
