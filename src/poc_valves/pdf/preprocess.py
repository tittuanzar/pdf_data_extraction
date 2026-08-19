from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import PageArtifact


class PdfProcessingError(RuntimeError):
    """Error raised during PDF processing."""
    pass


@dataclass(slots=True)
class PreprocessResult:
    """Result of PDF preprocessing."""

    pages: list[PageArtifact]
    rendered_images_dir: Path

    def assembled_text(self) -> str:
        """Assemble text from all pages with page markers."""
        chunks: list[str] = []
        for page in self.pages:
            chunks.append(f"<<<PAGE {page.page_number}>>>")
            chunks.append(page.text.strip())
        return "\n".join(chunks).strip()


def _optional_import_fitz() -> Any:
    """Import fitz (PyMuPDF) optionally."""
    try:
        import fitz  # type: ignore

        return fitz
    except Exception as exc:  # pragma: no cover - optional dependency
        raise PdfProcessingError(
            "PyMuPDF (fitz) is required for PDF preprocessing. "
            "Install the 'pdf' extra."
        ) from exc


def _optional_import_pdfplumber() -> Any | None:
    """Import pdfplumber optionally."""
    try:
        import pdfplumber  # type: ignore

        return pdfplumber
    except Exception:
        return None


def preprocess_pdf(
    pdf_path: str | Path,
    output_dir: str | Path,
    dpi: int = 150,
) -> PreprocessResult:
    """Preprocess a PDF file, extracting text and rendering pages."""
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    images_dir = output_dir / "pages"
    images_dir.mkdir(parents=True, exist_ok=True)

    fitz = _optional_import_fitz()
    pdfplumber = _optional_import_pdfplumber()

    document = fitz.open(pdf_path)
    pages: list[PageArtifact] = []

    plumber_doc = (
        pdfplumber.open(pdf_path) if pdfplumber else None
    )
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            text = page.get_text("text") or ""
            tables: list[list[list[str]]] = []
            if plumber_doc is not None:
                try:
                    extracted_tables = (
                        plumber_doc.pages[index].extract_tables()
                        or []
                    )
                    tables = [
                        [
                            [
                                (cell or "")
                                for cell in row
                            ]
                            for row in table
                        ]
                        for table in extracted_tables
                    ]
                except Exception:
                    tables = []

            page_rect = page.rect
            scale = dpi / 72.0
            matrix = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(
                matrix=matrix, alpha=False
            )
            image_path = (
                images_dir / f"page_{index + 1:03d}.png"
            )
            pix.save(image_path.as_posix())

            is_scanned = (
                len(text.strip()) < 40 and len(tables) == 0
            )
            pages.append(
                PageArtifact(
                    page_number=index + 1,
                    text=text,
                    tables=tables,
                    is_scanned=is_scanned,
                    image_path=image_path.as_posix(),
                )
            )
    finally:
        if plumber_doc is not None:
            plumber_doc.close()
        document.close()

    return PreprocessResult(
        pages=pages, rendered_images_dir=images_dir
    )