from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.models import PageArtifact
from poc_valves.pdf.preprocess import PreprocessResult
from poc_valves.pipeline.service import RequestedField, extract_requested_fields_from_preprocessed


class ServiceTests(unittest.TestCase):
    def test_heuristic_extraction(self) -> None:
        preprocessed = PreprocessResult(
            pages=[
                PageArtifact(
                    page_number=1,
                    text="Valve Number: VN-42\nDocument Number: DOC-7",
                    tables=[],
                    is_scanned=False,
                    image_path=None,
                )
            ],
            rendered_images_dir=Path(tempfile.gettempdir()),
        )

        result = extract_requested_fields_from_preprocessed(
            preprocessed,
            [
                RequestedField(field_id="valve_number", field_name="Valve Number"),
                RequestedField(field_id="document_number", field_name="Document Number"),
            ],
        )

        self.assertEqual(result.fields["valve_number"].value, "VN-42")
        self.assertEqual(result.fields["document_number"].value, "DOC-7")
        self.assertEqual(result.fields["valve_number"].source_page, 1)


if __name__ == "__main__":
    unittest.main()
