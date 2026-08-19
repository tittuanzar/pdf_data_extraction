from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PdfTextExtractionError(RuntimeError):
    """Error raised during PDF text extraction."""
    pass


@dataclass(slots=True)
class PdfPageText:
    """Text content from a single PDF page."""

    page_number: int
    text: str


@dataclass(slots=True)
class PdfPageTables:
    """Tables extracted from a single PDF page."""

    page_number: int
    tables: list[list[list[str]]]

    def to_dict(self) -> dict[str, object]:
        """Convert to a dictionary."""
        return {
            "page_number": self.page_number,
            "tables": self.tables,
        }


@dataclass(slots=True)
class PdfTextResult:
    """Result of PDF text extraction."""

    filename: str
    page_count: int
    pages: list[PdfPageText]

    @property
    def text(self) -> str:
        """Return assembled text from all pages."""
        return "\n".join(
            f"<<<PAGE {page.page_number}>>>\n"
            f"{page.text}".rstrip()
            for page in self.pages
        ).strip()

    def to_dict(self) -> dict[str, object]:
        """Convert to a dictionary."""
        return {
            "filename": self.filename,
            "page_count": self.page_count,
            "text": self.text,
            "pages": [
                {
                    "page_number": page.page_number,
                    "text": page.text,
                }
                for page in self.pages
            ],
        }


def _clean_table_rows(
    table: list[list[object | None]],
) -> list[list[str]]:
    """Clean table rows by converting cells to strings."""
    return [
        [
            "" if cell is None else str(cell).strip()
            for cell in row
        ]
        for row in table
    ]


def _optional_import_pdfplumber():
    """Import pdfplumber optionally."""
    try:
        import pdfplumber  # type: ignore

        return pdfplumber
    except Exception as exc:  # pragma: no cover - optional dependency
        raise PdfTextExtractionError(
            "pdfplumber is required for PDF text extraction. "
            "Install the 'pdf' extra."
        ) from exc


class PdfParser:
    """Parser for extracting text and tables from PDFs."""

    def __init__(self, pdf_path: str | Path) -> None:
        self.pdf_path = Path(pdf_path)
        self._pdfplumber = _optional_import_pdfplumber()

    def extract_text(self) -> PdfTextResult:
        """Extract text from all pages of the PDF."""
        pages: list[PdfPageText] = []
        with self._pdfplumber.open(self.pdf_path) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                pages.append(
                    PdfPageText(
                        page_number=index,
                        text=page.extract_text() or "",
                    )
                )
            page_count = len(pdf.pages)

        return PdfTextResult(
            filename=self.pdf_path.name,
            page_count=page_count,
            pages=pages,
        )

    def extract_tables_for_page(
        self,
        page_number: int,
        *,
        print_rows: bool = True,
    ) -> PdfPageTables:
        """Extract tables from a specific page."""
        with self._pdfplumber.open(self.pdf_path) as pdf:
            if (
                page_number < 1
                or page_number > len(pdf.pages)
            ):
                raise ValueError(
                    f"page_number must be between 1 and "
                    f"{len(pdf.pages)}"
                )

            page = pdf.pages[page_number - 1]
            tables = page.extract_tables() or []
            cleaned_tables = [
                _clean_table_rows(table)
                for table in tables
            ]

        if print_rows:
            if not cleaned_tables:
                print(
                    f"No table found on page {page_number}"
                )
            else:
                for table_index, table in enumerate(
                    cleaned_tables, start=1
                ):
                    print(
                        f"Table {table_index} on page "
                        f"{page_number}"
                    )
                    for row in table:
                        print(row)
                    print()

        return PdfPageTables(
            page_number=page_number,
            tables=cleaned_tables,
        )


def extract_pdf_text(
    pdf_path: str | Path,
) -> PdfTextResult:
    """Extract text from a PDF file."""
    return PdfParser(pdf_path).extract_text()


def extract_pdf_tables_for_page(
    pdf_path: str | Path,
    page_number: int,
    *,
    print_rows: bool = True,
) -> PdfPageTables:
    """Extract tables from a specific page of a PDF."""
    return PdfParser(pdf_path).extract_tables_for_page(
        page_number, print_rows=print_rows
    )