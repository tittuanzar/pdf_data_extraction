from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .pdf_text import PdfPageText, _optional_import_pdfplumber


TAG_PATTERN = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)+\b")
PROCESS_NOTES_PATTERN = re.compile(r"\bprocess notes\b", re.IGNORECASE)


@dataclass(slots=True)
class TaggedProductSection:
    tag_id: str
    start_page: int
    end_page: int
    pages: list[int]
    text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "tag_id": self.tag_id,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "pages": self.pages,
            "text": self.text,
        }


@dataclass(slots=True)
class TaggedProductResult:
    filename: str
    page_count: int
    pages: list[PdfPageText]
    sections: list[TaggedProductSection]

    @property
    def text(self) -> str:
        return "\n".join(
            f"<<<PAGE {page.page_number}>>>\n{page.text}".rstrip()
            for page in self.pages
        ).strip()

    def to_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "page_count": self.page_count,
            "text": self.text,
            "pages": [
                {"page_number": page.page_number, "text": page.text}
                for page in self.pages
            ],
            "sections": [section.to_dict() for section in self.sections],
        }


def extract_tag_ids(text: str) -> list[str]:
    seen: list[str] = []
    for match in TAG_PATTERN.finditer(text):
        tag = match.group(0)
        if tag not in seen:
            seen.append(tag)
    return seen


def _best_tag_for_page(page_text: str) -> str | None:
    tags = extract_tag_ids(page_text)
    return tags[0] if tags else None


def build_tagged_product_sections(pages: list[PdfPageText]) -> list[TaggedProductSection]:
    sections: list[TaggedProductSection] = []
    current_tag: str | None = None
    current_pages: list[PdfPageText] = []
    current_start_page: int | None = None

    def flush() -> None:
        nonlocal current_tag, current_pages, current_start_page
        if not current_tag or not current_pages or current_start_page is None:
            current_tag = None
            current_pages = []
            current_start_page = None
            return
        sections.append(
            TaggedProductSection(
                tag_id=current_tag,
                start_page=current_start_page,
                end_page=current_pages[-1].page_number,
                pages=[page.page_number for page in current_pages],
                text="\n".join(
                    f"<<<PAGE {page.page_number}>>>\n{page.text}".rstrip()
                    for page in current_pages
                ).strip(),
            )
        )
        current_tag = None
        current_pages = []
        current_start_page = None

    for page in pages:
        page_text = page.text or ""
        page_tag = _best_tag_for_page(page_text)
        has_process_notes = bool(PROCESS_NOTES_PATTERN.search(page_text))

        if page_tag and page_tag != current_tag:
            flush()
            current_tag = page_tag
            current_start_page = page.page_number
            current_pages = [page]
        else:
            if current_tag is None and page_tag:
                current_tag = page_tag
                current_start_page = page.page_number
                current_pages = [page]
            elif current_tag is not None:
                current_pages.append(page)

        if has_process_notes and current_tag is not None:
            flush()

    flush()
    return sections


def extract_tagged_products(pdf_path: str | Path) -> TaggedProductResult:
    pdf_path = Path(pdf_path)
    pdfplumber = _optional_import_pdfplumber()

    pages: list[PdfPageText] = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            pages.append(PdfPageText(page_number=index, text=page.extract_text() or ""))
        page_count = len(pdf.pages)

    sections = build_tagged_product_sections(pages)
    return TaggedProductResult(
        filename=pdf_path.name,
        page_count=page_count,
        pages=pages,
        sections=sections,
    )
