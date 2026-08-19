from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PageText:
    """Text content from a single PDF page."""

    page_number: int
    text: str


@dataclass(slots=True)
class LlmChunkResult:
    """Result of LLM extraction for a chunk of pages."""

    rows: list[dict[str, Any]]
    raw_response: str
    chunk_label: str
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReviewItem:
    """An item that requires manual review."""

    serial_number: str
    reason: str
    row: dict[str, Any]


@dataclass(slots=True)
class FieldMappingRow:
    """A single row from the field mapping register."""

    data: dict[str, Any]

    @property
    def serial_number(self) -> str:
        """Return the serial number for this row."""
        return str(
            self.data.get("serial_number", "")
        ).strip()


@dataclass(slots=True)
class FieldMappingDocument:
    """Complete field mapping register document."""

    source_pdf: str
    page_texts: list[PageText]
    tag_ids: list[str]
    rows: list[FieldMappingRow]
    review_items: list[ReviewItem] = field(
        default_factory=list
    )
    warnings: list[str] = field(default_factory=list)