from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .models import FieldDefinition, SchemaDefinition


class SchemaError(ValueError):
    pass


def load_schema(schema_path: str | Path) -> SchemaDefinition:
    path = Path(schema_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = SchemaDefinition.from_dict(data)
    validate_schema_contract(schema)
    return schema


def validate_schema_contract(
    schema: SchemaDefinition,
    *,
    require_complete: bool = False,
) -> None:
    if require_complete:
        if schema.expected_field_count is not None and len(schema.fields) != schema.expected_field_count:
            raise SchemaError(
                f"Expected {schema.expected_field_count} fields, found {len(schema.fields)}"
            )
        if (
            schema.expected_subcategory_count is not None
            and len(schema.subcategories) != schema.expected_subcategory_count
        ):
            raise SchemaError(
                f"Expected {schema.expected_subcategory_count} subcategories, found {len(schema.subcategories)}"
            )

    seen_ids: set[str] = set()
    for field in schema.fields:
        if field.field_id in seen_ids:
            raise SchemaError(f"Duplicate field_id detected: {field.field_id}")
        seen_ids.add(field.field_id)
        if not field.subcategory:
            raise SchemaError(f"Field {field.field_id} is missing a subcategory")
        if field.data_type not in {"string", "number", "date", "enum", "boolean", "object", "array"}:
            raise SchemaError(f"Field {field.field_id} has invalid data_type: {field.data_type}")


def group_fields_by_subcategory(schema: SchemaDefinition) -> dict[str, list[FieldDefinition]]:
    grouped: dict[str, list[FieldDefinition]] = defaultdict(list)
    for field in schema.fields:
        grouped[field.subcategory].append(field)
    return dict(grouped)


def build_field_index(schema: SchemaDefinition) -> dict[str, FieldDefinition]:
    return {field.field_id: field for field in schema.fields}


def all_subcategories(schema: SchemaDefinition) -> list[str]:
    if schema.subcategories:
        return list(schema.subcategories)
    grouped = group_fields_by_subcategory(schema)
    return sorted(grouped)


def iter_required_fields(schema: SchemaDefinition) -> Iterable[FieldDefinition]:
    return (field for field in schema.fields if field.required)

