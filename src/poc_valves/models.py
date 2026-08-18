from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


DataType = Literal["string", "number", "date", "enum", "boolean", "object", "array"]
IssueStatus = Literal["ok", "missing", "type_error", "unverified", "schema_error"]


@dataclass(slots=True)
class FieldDefinition:
    field_id: str
    field_name: str
    subcategory: str
    data_type: DataType = "string"
    unit: str | None = None
    required: bool = False
    validation_regex: str | None = None
    description: str | None = None
    enum_values: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldDefinition":
        return cls(
            field_id=str(data["field_id"]),
            field_name=str(data["field_name"]),
            subcategory=str(data["subcategory"]),
            data_type=data.get("data_type", "string"),
            unit=data.get("unit"),
            required=bool(data.get("required", False)),
            validation_regex=data.get("validation_regex"),
            description=data.get("description"),
            enum_values=list(data.get("enum_values", [])),
        )


@dataclass(slots=True)
class SchemaDefinition:
    document_type: str
    version: str
    expected_field_count: int | None
    expected_subcategory_count: int | None
    fields: list[FieldDefinition]
    subcategories: list[str]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchemaDefinition":
        fields = [FieldDefinition.from_dict(item) for item in data.get("fields", [])]
        subcategories = list(data.get("subcategories", []))
        return cls(
            document_type=str(data.get("document_type", "unknown")),
            version=str(data.get("version", "0.0.0")),
            expected_field_count=data.get("expected_field_count"),
            expected_subcategory_count=data.get("expected_subcategory_count"),
            fields=fields,
            subcategories=subcategories,
            raw=data,
        )


@dataclass(slots=True)
class PageArtifact:
    page_number: int
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    is_scanned: bool = False
    image_path: str | None = None


@dataclass(slots=True)
class ExtractedField:
    field_id: str
    value: Any
    source_page: int | None = None
    confidence: float | None = None
    notes: str | None = None
    normalized_value: Any | None = None


@dataclass(slots=True)
class ValidationIssue:
    field_id: str
    status: IssueStatus
    message: str
    source_page: int | None = None


@dataclass(slots=True)
class DocumentResult:
    document_name: str
    metadata: dict[str, Any]
    fields: dict[str, ExtractedField]
    validation: list[ValidationIssue]
    raw_responses: dict[str, Any] = field(default_factory=dict)
    page_artifacts: list[PageArtifact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_name": self.document_name,
            "metadata": self.metadata,
            "fields": {
                field_id: {
                    "field_id": item.field_id,
                    "value": item.value,
                    "normalized_value": item.normalized_value,
                    "source_page": item.source_page,
                    "confidence": item.confidence,
                    "notes": item.notes,
                }
                for field_id, item in self.fields.items()
            },
            "validation": [
                {
                    "field_id": issue.field_id,
                    "status": issue.status,
                    "message": issue.message,
                    "source_page": issue.source_page,
                }
                for issue in self.validation
            ],
            "page_artifacts": [
                {
                    "page_number": page.page_number,
                    "text": page.text,
                    "tables": page.tables,
                    "is_scanned": page.is_scanned,
                    "image_path": page.image_path,
                }
                for page in self.page_artifacts
            ],
            "raw_responses": self.raw_responses,
        }
