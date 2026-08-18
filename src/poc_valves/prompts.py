from __future__ import annotations

from .models import FieldDefinition, SchemaDefinition


SYSTEM_PROMPT = (
    "You extract fixed-format structured values from PDF source text. "
    "Return only valid JSON. Never guess. Use null when a value cannot be found. "
    "Provide source_page for every field."
)


def build_subcategory_prompt(
    schema: SchemaDefinition,
    subcategory: str,
    fields: list[FieldDefinition],
    document_text: str,
) -> str:
    field_lines: list[str] = []
    for field in fields:
        field_lines.append(
            f"- {field.field_id} | {field.field_name} | type={field.data_type}"
            + (f" | unit={field.unit}" if field.unit else "")
            + (" | required=true" if field.required else "")
            + (f" | regex={field.validation_regex}" if field.validation_regex else "")
        )

    return "\n".join(
        [
            SYSTEM_PROMPT,
            "",
            f"Document type: {schema.document_type}",
            f"Schema version: {schema.version}",
            f"Subcategory: {subcategory}",
            "",
            "Extract only the following fields:",
            *field_lines,
            "",
            "Document text with page markers:",
            document_text,
            "",
            "Return JSON in the form:",
            "{",
            '  "subcategory": "...",',
            '  "fields": [',
            '    {"field_id": "...", "value": null, "source_page": 1, "confidence": 0.0, "notes": null}',
            "  ]",
            "}",
        ]
    )

