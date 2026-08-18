from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PageText:
    page_number: int
    text: str


@dataclass(slots=True)
class LlmChunkResult:
    rows: list[dict[str, Any]]
    raw_response: str
    chunk_label: str
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReviewItem:
    serial_number: str
    reason: str
    row: dict[str, Any]


@dataclass(slots=True)
class FieldMappingRow:
    data: dict[str, Any]

    @property
    def serial_number(self) -> str:
        return str(self.data.get("serial_number", "")).strip()


@dataclass(slots=True)
class FieldMappingDocument:
    source_pdf: str
    page_texts: list[PageText]
    tag_ids: list[str]
    rows: list[FieldMappingRow]
    review_items: list[ReviewItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

