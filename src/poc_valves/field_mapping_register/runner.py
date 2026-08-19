from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .build_excel import build_excel
from .extract_pdf_text import (
    PdfTextExtractionError,
    extract_pdf_text,
)
from .llm_extract_rows import (
    OpenAIConfig,
    chunk_pages,
    extract_rows_from_chunk,
)
from .models import (
    FieldMappingDocument,
    FieldMappingRow,
    PageText,
    ReviewItem,
)


DEFAULT_PIPE_SPLIT_PROTECTED = {
    "serial_number",
    "enquiry_field_name",
    "mapping_logic",
    "units",
    "category",
    "requirement",
    "similar_field_sv2_field",
    "similar_field_dependency",
    "sv2_spec_dependency_terminologies",
    "row_number",
    "field_name",
    "validations",
    "notes_and_additional_information",
    "llm_confidence",
    "source_pages",
}


def _normalize_cell(value: Any) -> Any:
    """Normalize a cell value."""
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value


def infer_tag_ids(
    page_texts: list[PageText], expected_tags: int = 3
) -> list[str]:
    """Infer tag IDs from page texts."""
    import re

    tag_pattern = r"[A-Z0-9]+(?:-[A-Z0-9]+)+"
    best: list[str] = []
    best_score = 0
    for page in page_texts:
        for line in page.text.splitlines():
            parts = [
                part.strip()
                for part in line.split("|")
            ]
            if len(parts) < expected_tags:
                continue

            matches = [
                part
                for part in parts
                if re.fullmatch(tag_pattern, part)
            ]
            score = len(matches)
            if score >= expected_tags and score > best_score:
                best = parts[:expected_tags]
                best_score = score
    if best:
        return best
    return [
        f"TAG{i}" for i in range(1, expected_tags + 1)
    ]


def merge_rows(
    chunks: list[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge rows from multiple chunks."""
    merged: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for chunk_rows in chunks:
        for row in chunk_rows:
            serial_number = str(
                row.get("serial_number", "")
            ).strip()
            if not serial_number:
                warnings.append(
                    "Skipped row without serial_number"
                )
                continue
            existing = merged.setdefault(serial_number, {})
            for key, value in row.items():
                value = _normalize_cell(value)
                if key == "serial_number":
                    existing[key] = serial_number
                    continue
                if value in (None, ""):
                    continue
                current = existing.get(key)
                if current in (None, ""):
                    existing[key] = value
                    continue
                if (
                    isinstance(current, str)
                    and isinstance(value, str)
                    and len(value) > len(current)
                ):
                    existing[key] = value
            pages = existing.get("source_pages", [])
            if not isinstance(pages, list):
                pages = [pages]
            row_pages = row.get("source_pages", [])
            if isinstance(row_pages, list):
                pages.extend(row_pages)
            elif row_pages not in (None, ""):
                pages.append(row_pages)
            existing["source_pages"] = sorted(
                {
                    str(item)
                    for item in pages
                    if item not in (None, "")
                }
            )
    return list(merged.values()), warnings


def _split_pipe_values(
    row: dict[str, Any], tag_ids: list[str]
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Split pipe-separated values into per-tag columns."""
    output = dict(row)
    review_reasons: list[str] = []
    warnings: list[str] = []

    for key, value in list(row.items()):
        if key in DEFAULT_PIPE_SPLIT_PROTECTED:
            continue
        if (
            not isinstance(value, str)
            or "|" not in value
        ):
            continue

        parts = [
            part.strip() for part in value.split("|")
        ]
        if len(parts) != len(tag_ids):
            warnings.append(
                f"{key} did not split cleanly into "
                f"{len(tag_ids)} tag values"
            )
            review_reasons.append(f"{key} split mismatch")
            continue

        output.pop(key, None)
        for tag_id, part in zip(tag_ids, parts):
            output[f"{key}_{tag_id}"] = part or None

    return output, review_reasons, warnings


def normalize_rows(
    rows: list[dict[str, Any]],
    tag_ids: list[str],
) -> tuple[
    list[dict[str, Any]],
    list[ReviewItem],
    list[str],
]:
    """Normalize rows and identify items needing review."""
    normalized_rows: list[dict[str, Any]] = []
    review_items: list[ReviewItem] = []
    warnings: list[str] = []

    for row in rows:
        normalized_row = dict(row)
        split_row, split_reasons, split_warnings = (
            _split_pipe_values(normalized_row, tag_ids)
        )
        warnings.extend(split_warnings)
        confidence = split_row.get("llm_confidence")
        review_needed = False
        review_reason_parts: list[str] = []

        if (
            isinstance(confidence, (int, float))
            and confidence < 0.8
        ):
            review_needed = True
            review_reason_parts.append(
                f"low confidence ({confidence})"
            )
        if split_reasons:
            review_needed = True
            review_reason_parts.extend(split_reasons)

        split_row["review_flag"] = (
            "yes" if review_needed else "no"
        )
        split_row["review_reason"] = (
            "; ".join(review_reason_parts)
            if review_reason_parts
            else ""
        )
        normalized_rows.append(split_row)

        if review_needed:
            review_items.append(
                ReviewItem(
                    serial_number=str(
                        split_row.get("serial_number", "")
                    ),
                    reason=split_row["review_reason"],
                    row=split_row,
                )
            )

    return normalized_rows, review_items, warnings


def preview_rows(
    rows: list[dict[str, Any]], count: int = 3
) -> None:
    """Preview extracted rows."""
    sample = rows[:count]
    print("\nPreview of extracted rows:")
    print(
        json.dumps(
            sample, indent=2, ensure_ascii=False
        )
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="poc-valves field-mapping-register"
    )
    parser.add_argument(
        "--pdf", required=True, help="Input PDF file"
    )
    parser.add_argument(
        "--output",
        default="field_mapping_register.xlsx",
        help="Output Excel file path",
    )
    parser.add_argument(
        "--pages-per-chunk",
        type=int,
        default=2,
        help="Number of PDF pages to send per LLM request",
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help=(
            "Print a preview and stop before writing Excel"
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Override OpenAI model name "
            "(defaults to OPENAI_MODEL or gpt-4.1)"
        ),
    )
    return parser


def run(
    pdf_path: str | Path,
    output_path: str | Path,
    pages_per_chunk: int = 2,
    model: str | None = None,
) -> FieldMappingDocument:
    """Run the field mapping register pipeline."""
    return run_pipeline(
        pdf_path,
        output_path,
        pages_per_chunk=pages_per_chunk,
        model=model,
        preview_only=False,
    )


def run_pipeline(
    pdf_path: str | Path,
    output_path: str | Path,
    *,
    pages_per_chunk: int = 2,
    model: str | None = None,
    preview_only: bool = False,
) -> FieldMappingDocument:
    """Run the field mapping register pipeline."""
    pdf_path = Path(pdf_path)
    page_texts = extract_pdf_text(pdf_path)
    tag_ids = infer_tag_ids(page_texts)

    config = OpenAIConfig.from_env()
    if model:
        config.model = model

    chunks = chunk_pages(page_texts, pages_per_chunk)
    chunk_results = []
    chunk_warnings: list[str] = []
    for chunk in chunks:
        result = extract_rows_from_chunk(
            config, chunk, max_parse_retries=1
        )
        chunk_results.append(result.rows)
        chunk_warnings.extend(result.warnings)

    merged_rows, merge_warnings = merge_rows(chunk_results)
    normalized_rows, review_items, split_warnings = (
        normalize_rows(merged_rows, tag_ids)
    )

    document = FieldMappingDocument(
        source_pdf=pdf_path.as_posix(),
        page_texts=page_texts,
        tag_ids=tag_ids,
        rows=[
            FieldMappingRow(data=row)
            for row in normalized_rows
        ],
        review_items=review_items,
        warnings=(
            chunk_warnings
            + merge_warnings
            + split_warnings
        ),
    )

    preview_rows(normalized_rows, count=3)
    if preview_only:
        print("\nPreview-only mode: skipping Excel write.")
        return document

    build_excel(
        output_path,
        normalized_rows,
        review_items=review_items,
    )
    print(
        f"\nSaved {output_path} with "
        f"{len(normalized_rows)} rows. "
        f"Warnings: {len(document.warnings)}. "
        f"Review rows: {len(review_items)}."
    )
    return document


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        run_pipeline(
            args.pdf,
            args.output,
            pages_per_chunk=args.pages_per_chunk,
            model=args.model,
            preview_only=args.preview_only,
        )
    except PdfTextExtractionError as exc:
        raise SystemExit(str(exc)) from exc
    except Exception as exc:
        raise SystemExit(
            f"Extraction failed: {exc}"
        ) from exc
    return 0