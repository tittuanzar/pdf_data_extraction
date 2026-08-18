from __future__ import annotations

from pathlib import Path

from .models import PageText


class PdfTextExtractionError(RuntimeError):
    pass


def _optional_import_pdfplumber():
    try:
        import pdfplumber  # type: ignore

        return pdfplumber
    except Exception as exc:  # pragma: no cover - optional dependency
        raise PdfTextExtractionError(
            "pdfplumber is required. Install the 'pdf' extra or run `uv sync --extra pdf`."
        ) from exc


def extract_pdf_text(pdf_path: str | Path) -> list[PageText]:
    pdf_path = Path(pdf_path)
    pdfplumber = _optional_import_pdfplumber()

    page_texts: list[PageText] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            page_texts.append(PageText(page_number=page_number, text=page.extract_text() or ""))

    return page_texts

