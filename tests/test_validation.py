from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.models import DocumentResult, ExtractedField, FieldDefinition, PageArtifact, SchemaDefinition
from poc_valves.validation import validate_result


class ValidationTests(unittest.TestCase):
    def test_numeric_and_regex_validation(self) -> None:
        schema = SchemaDefinition(
            document_type="demo",
            version="1",
            expected_field_count=1,
            expected_subcategory_count=1,
            fields=[
                FieldDefinition(
                    field_id="doc_no",
                    field_name="Document No",
                    subcategory="meta",
                    data_type="number",
                    required=True,
                    validation_regex=r"\d+",
                )
            ],
            subcategories=["meta"],
        )
        result = DocumentResult(
            document_name="x.pdf",
            metadata={},
            fields={"doc_no": ExtractedField(field_id="doc_no", value="123", source_page=1)},
            validation=[],
            page_artifacts=[],
        )
        validated = validate_result(schema, result, [PageArtifact(page_number=1, text="Document No: 123")])
        self.assertEqual(validated.fields["doc_no"].normalized_value, 123)
        self.assertEqual(validated.validation[0].status, "ok")


if __name__ == "__main__":
    unittest.main()
