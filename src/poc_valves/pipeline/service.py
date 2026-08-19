from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..models import DocumentResult, ExtractedField, FieldDefinition, SchemaDefinition
from ..pdf.preprocess import PreprocessResult, preprocess_pdf
from ..schema.schema import validate_schema_contract
from .validation import validate_result


@dataclass(slots=True)
class RequestedField:
    field_id: str
    field_name: str
    data_type: str = "string"
    required: bool = False
    validation_regex: str | None = None
    unit: str | None = None
    description: str | None = None
    aliases: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RequestedField":
        return cls(
            field_id=str(data["field_id"]),
            field_name=str(data.get("field_name") or data["field_id"]),
            data_type=str(data.get("data_type", "string")),
            required=bool(data.get("required", False)),
            validation_regex=data.get("validation_regex"),
            unit=data.get("unit"),
            description=data.get("description"),
            aliases=list(data.get("aliases", [])) if data.get("aliases") else None,
        )

    def to_field_definition(self, subcategory: str = "requested") -> FieldDefinition:
        return FieldDefinition(
            field_id=self.field_id,
            field_name=self.field_name,
            subcategory=subcategory,
            data_type=self.data_type,  # type: ignore[arg-type]
            unit=self.unit,
            required=self.required,
            validation_regex=self.validation_regex,
            description=self.description,
        )


def build_requested_schema(requested_fields: Iterable[RequestedField]) -> SchemaDefinition:
    field_defs = [item.to_field_definition() for item in requested_fields]
    return SchemaDefinition(
        document_type="ad_hoc_requested_fields",
        version="1.0.0",
        expected_field_count=len(field_defs),
        expected_subcategory_count=1 if field_defs else 0,
        fields=field_defs,
        subcategories=["requested"] if field_defs else [],
    )


def _normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _candidate_labels(field: RequestedField) -> list[str]:
    labels = [field.field_name, field.field_id]
    if field.aliases:
        labels.extend(field.aliases)
    return [label for label in labels if label]


def _label_pattern(label: str) -> str:
    parts = [re.escape(part) for part in _normalize_label(label).split()]
    return r"\s+".join(parts)


def _extract_value_from_page(page_text: str, labels: list[str]) -> str | None:
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    normalized_lines = [(_normalize_label(line), line) for line in lines]

    for label in labels:
        normalized_label = _normalize_label(label)
        label_pattern = _label_pattern(label)
        for normalized_line, original_line in normalized_lines:
            if normalized_label not in normalized_line:
                continue

            patterns = [
                rf"(?i)^\s*{label_pattern}\s*[:=\-]\s*(.+)$",
                rf"(?i)^\s*{label_pattern}\s+(.+)$",
            ]
            for pattern in patterns:
                match = re.search(pattern, original_line)
                if match:
                    value = match.group(1).strip()
                    if value:
                        return value

            index = normalized_lines.index((normalized_line, original_line))
            tail = original_line.split(":", 1)
            if len(tail) == 2 and tail[1].strip():
                return tail[1].strip()
            if index + 1 < len(lines):
                return lines[index + 1].strip()
    return None


def _coerce_heuristic_value(data_type: str, value: Any) -> Any:
    if value is None:
        return None
    if data_type == "number":
        text = str(value).strip().replace(",", "")
        try:
            return int(text) if text.isdigit() else float(text)
        except ValueError:
            return value
    if data_type == "boolean":
        text = str(value).strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    return value


def extract_requested_fields_from_preprocessed(
    preprocessed: PreprocessResult,
    requested_fields: list[RequestedField],
) -> DocumentResult:
    schema = build_requested_schema(requested_fields)
    validate_schema_contract(schema, require_complete=False)

    extracted: dict[str, ExtractedField] = {}
    for field in requested_fields:
        labels = _candidate_labels(field)
        found_value: Any = None
        found_page: int | None = None

        for page in preprocessed.pages:
            found_value = _extract_value_from_page(page.text, labels)
            if found_value is None and page.tables:
                table_text = "\n".join(
                    " | ".join(cell for cell in row if cell is not None)
                    for table in page.tables
                    for row in table
                )
                found_value = _extract_value_from_page(table_text, labels)
            if found_value is not None:
                found_page = page.page_number
                break

        normalized = _coerce_heuristic_value(field.data_type, found_value)
        extracted[field.field_id] = ExtractedField(
            field_id=field.field_id,
            value=normalized,
            source_page=found_page,
            confidence=0.65 if found_value is not None else None,
            notes="heuristic extraction",
        )

    result = DocumentResult(
        document_name="uploaded_document.pdf",
        metadata={"mode": "heuristic", "requested_field_count": len(requested_fields)},
        fields=extracted,
        validation=[],
        page_artifacts=preprocessed.pages,
    )
    return validate_result(schema, result, preprocessed.pages)


def run_requested_pdf_extraction(
    pdf_path: str | Path,
    requested_fields: list[RequestedField],
    *,
    output_dir: str | Path,
    render_dpi: int = 150,
) -> DocumentResult:
    preprocessed = preprocess_pdf(pdf_path, output_dir, dpi=render_dpi)
    result = extract_requested_fields_from_preprocessed(preprocessed, requested_fields)
    result.document_name = Path(pdf_path).name
    result.metadata.update({"source_pdf": Path(pdf_path).as_posix(), "render_dpi": render_dpi})
    return result
