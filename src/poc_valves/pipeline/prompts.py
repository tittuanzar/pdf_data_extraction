from __future__ import annotations

from ..config import get_prompt
from ..models import FieldDefinition, SchemaDefinition


def build_subcategory_prompt(
    schema: SchemaDefinition,
    subcategory: str,
    fields: list[FieldDefinition],
    document_text: str,
) -> str:
    """Build a prompt for extracting fields from a subcategory."""
    field_lines: list[str] = []
    for field in fields:
        field_lines.append(
            f"- {field.field_id} | {field.field_name} "
            f"| type={field.data_type}"
            + (f" | unit={field.unit}" if field.unit else "")
            + (
                " | required=true"
                if field.required
                else ""
            )
            + (
                f" | regex={field.validation_regex}"
                if field.validation_regex
                else ""
            )
        )

    system_prompt = get_prompt("subcategory_extraction.system_prompt")
    user_prompt = get_prompt(
        "subcategory_extraction.user_prompt",
        document_type=schema.document_type,
        schema_version=schema.version,
        subcategory=subcategory,
        field_lines="\n".join(field_lines),
        document_text=document_text,
    )

    return f"{system_prompt}\n\n{user_prompt}"
