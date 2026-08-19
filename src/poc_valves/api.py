from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
except Exception as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "FastAPI is not installed. Install the 'api' extra to run the web application."
    ) from exc

from .pdf.pdf_text import extract_pdf_text, extract_pdf_tables_for_page
from .pipeline.sv2_pipeline import extract_sv2_output_from_pages

logger = logging.getLogger(__name__)

ALLOWED_MIME = "application/pdf"
ALLOWED_EXTENSIONS = {".pdf"}


app = FastAPI(title="Poc Valves PDF Text API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _validate_pdf(pdf: UploadFile) -> None:
    """Raise HTTPException 400 if the upload is not a PDF."""
    filename = pdf.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are accepted. Got extension '{ext or '(none)'}'.",
        )
    content_type = (pdf.content_type or "").lower()
    if content_type and content_type != ALLOWED_MIME and "pdf" not in content_type:
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are accepted. Got content-type '{content_type}'.",
        )


def _build_excel(parsed) -> io.BytesIO:
    """Convert ParsedOutput into an Excel workbook and return as BytesIO.

    Each extracted tag gets its own sheet named after its tag_no.
    Columns: Sl.No | Feature name | Extracted feature value
    """
    wb = Workbook()

    if not parsed.tags:
        ws = wb.active
        ws.title = "tags extracted"
        ws.append(["Sl.No", "Feature name", "Extracted feature value"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    wb.remove(wb.active)
    for tag in parsed.tags:
        sheet_name = str(tag.tag_no) or "Unknown"
        ws = wb.create_sheet(title=sheet_name[:31])

        ws.append(["Sl.No", "Feature name", "Extracted feature value"])

        field_values = tag.model_dump()
        for sl, (field_name, value) in enumerate(field_values.items(), start=1):
            display_value = "" if value is None else value
            ws.append([sl, field_name, display_value])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@app.post("/extract-Tag-wise-Enquiry-pdf")
async def sv2_extract(pdf: UploadFile = File(...)) -> StreamingResponse:
    """Accept a PDF file and return an Excel workbook with extracted tags."""
    _validate_pdf(pdf)

    suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.TemporaryDirectory(prefix="poc-valves-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        pdf_path = tmpdir_path / f"upload{suffix}"
        pdf_bytes = await pdf.read()
        pdf_path.write_bytes(pdf_bytes)
        logger.info("SV2 extract: received file %s -> %s", pdf.filename, pdf_path)

        try:
            text_result = extract_pdf_text(pdf_path)
            pages_meta = text_result.to_dict()["pages"]
            pages = []
            for p in pages_meta:
                pn = p["page_number"]
                try:
                    tables_obj = extract_pdf_tables_for_page(pdf_path, pn, print_rows=False)
                    tables = tables_obj.to_dict()["tables"]
                except Exception:
                    tables = []
                pages.append({"page_number": pn, "text": p["text"], "image_b64": "", "tables": tables})

            parsed = extract_sv2_output_from_pages(pages, guide=None, debug=False)
        except Exception as exc:
            logger.exception("SV2 extraction failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    excel_buf = _build_excel(parsed)
    output_filename = Path(pdf.filename or "output").stem + "_tags_extracted.xlsx"

    return StreamingResponse(
        excel_buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{output_filename}"',
        },
    )


def create_app() -> FastAPI:
    return app
