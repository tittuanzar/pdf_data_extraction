from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_valves.models import FieldDefinition, SchemaDefinition
from poc_valves.pydantic_output import ParsedOutput
from poc_valves.schema.schema import (
    build_field_index,
    group_fields_by_subcategory,
    validate_schema_contract,
)
from poc_valves.pipeline.sv2_pipeline import (
    extract_sv2_output_from_pages,
)


class SchemaTests(unittest.TestCase):
    """Tests for schema operations."""

    def test_grouping_and_indexing(self) -> None:
        """Test field grouping and indexing."""
        schema = SchemaDefinition(
            document_type="demo",
            version="1",
            expected_field_count=2,
            expected_subcategory_count=2,
            fields=[
                FieldDefinition(
                    field_id="a",
                    field_name="A",
                    subcategory="one",
                ),
                FieldDefinition(
                    field_id="b",
                    field_name="B",
                    subcategory="two",
                ),
            ],
            subcategories=["one", "two"],
        )
        self.assertEqual(
            sorted(group_fields_by_subcategory(schema)),
            ["one", "two"],
        )
        self.assertEqual(
            set(build_field_index(schema)), {"a", "b"}
        )
        validate_schema_contract(schema, require_complete=True)

    def test_schema_json_roundtrip(self) -> None:
        """Test schema JSON roundtrip."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            path.write_text(
                json.dumps(
                    {
                        "document_type": "demo",
                        "version": "1",
                        "expected_field_count": 0,
                        "expected_subcategory_count": 0,
                        "fields": [],
                        "subcategories": [],
                    }
                ),
                encoding="utf-8",
            )
            from poc_valves.schema.schema import load_schema

            schema = load_schema(path)
            self.assertEqual(schema.document_type, "demo")

    @patch(
        "poc_valves.pipeline.sv2_pipeline._get_client"
    )
    def test_extract_sv2_output_uses_parsed_output_schema(
        self, mock_get_client
    ):
        """Test that SV2 extraction uses ParsedOutput schema."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    parsed=ParsedOutput(tags=[])
                )
            )
        ]
        mock_client.chat.completions.parse.return_value = (
            mock_response
        )
        mock_get_client.return_value = mock_client

        result = extract_sv2_output_from_pages(
            [
                {
                    "page_number": 1,
                    "text": "sample text",
                    "image_b64": "",
                    "tables": [],
                }
            ],
            guide="guide",
        )

        self.assertIsInstance(result, ParsedOutput)
        self.assertEqual(result.tags, [])
        parse_kwargs = (
            mock_client.chat.completions.parse.call_args.kwargs
        )
        self.assertIs(
            parse_kwargs["response_format"], ParsedOutput
        )


if __name__ == "__main__":
    unittest.main()