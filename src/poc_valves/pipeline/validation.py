from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from typing import Any

from ..models import (
    DocumentResult,
    ExtractedField,
    FieldDefinition,
    PageArtifact,
    SchemaDefinition,
    ValidationIssue,
)


def _coerce_value(
    field: FieldDefinition, value: Any
) -> Any:
    """Coerce a value to the field's data type."""
    if value is None:
        return None
    if field.data_type == "number":
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return (
                int(text) if text.isdigit() else float(text)
            )
        except ValueError:
            return value
    if field.data_type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
        return value
    if field.data_type == "date":
        text = str(value).strip()
        for fmt in (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return value
    return value


def _value_matches_regex(
    field: FieldDefinition, value: Any
) -> bool:
    """Check if a value matches the field's validation regex."""
    if value is None or not field.validation_regex:
        return True
    return (
        re.fullmatch(field.validation_regex, str(value))
        is not None
    )


def validate_result(
    schema: SchemaDefinition,
    result: DocumentResult,
    page_artifacts: list[PageArtifact],
) -> DocumentResult:
    """Validate extraction results against the schema."""
    validated_fields: dict[str, ExtractedField] = {}
    issues: list[ValidationIssue] = []
    page_lookup = {
        page.page_number: page for page in page_artifacts
    }

    for field_def in schema.fields:
        extracted = result.fields.get(field_def.field_id)
        if extracted is None:
            status = (
                "missing" if field_def.required else "unverified"
            )
            issues.append(
                ValidationIssue(
                    field_id=field_def.field_id,
                    status=status,
                    message="Field was not extracted",
                    source_page=None,
                )
            )
            continue

        normalized = _coerce_value(field_def, extracted.value)
        status: str = "ok"
        message = "Field validated"

        if extracted.value is None:
            status = (
                "missing"
                if field_def.required
                else "unverified"
            )
            message = "No value provided"
        elif (
            field_def.data_type == "number"
            and not isinstance(normalized, (int, float))
        ):
            status = "type_error"
            message = "Expected a numeric value"
        elif (
            field_def.data_type == "boolean"
            and not isinstance(normalized, bool)
        ):
            status = "type_error"
            message = "Expected a boolean value"
        elif (
            field_def.data_type == "date"
            and not isinstance(normalized, str)
        ):
            status = "type_error"
            message = "Expected a normalized ISO date"
        elif not _value_matches_regex(field_def, normalized):
            status = "type_error"
            message = "Value failed regex validation"

        if (
            extracted.source_page is not None
            and extracted.source_page in page_lookup
        ):
            page_text = page_lookup[extracted.source_page].text
            if (
                normalized is not None
                and str(normalized).strip()
                and str(normalized) not in page_text
            ):
                if status == "ok":
                    status = "unverified"
                    message = (
                        "Value not found on cited page text"
                    )

        validated_fields[field_def.field_id] = replace(
            extracted, normalized_value=normalized
        )
        issues.append(
            ValidationIssue(
                field_id=field_def.field_id,
                status=status,  # type: ignore[arg-type]
                message=message,
                source_page=extracted.source_page,
            )
        )

    return DocumentResult(
        document_name=result.document_name,
        metadata=result.metadata,
        fields=validated_fields,
        validation=issues,
        raw_responses=result.raw_responses,
        page_artifacts=page_artifacts,
    )