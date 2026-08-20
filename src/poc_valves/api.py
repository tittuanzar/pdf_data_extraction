from __future__ import annotations

import logging
import tempfile
from pathlib import Path

try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
except Exception as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "FastAPI is not installed. Install the 'api' extra to run the web application."
    ) from exc

from .core.excel import ExcelWriter
from .core.pdf import extract_pdf_tables_for_page, extract_pdf_text
from .core.pipeline import SV2Pipeline

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


@app.on_event("startup")
async def _log_config() -> None:
    pipeline = SV2Pipeline()
    logger.info(
        "Startup config — model: %s, temperature: %s",
        pipeline.extractor.llm.model,
        pipeline.extractor.llm.temperature,
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
    if (
        content_type
        and content_type != ALLOWED_MIME
        and "pdf" not in content_type
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are accepted. Got content-type '{content_type}'.",
        )


@app.post("/extract-Tag-wise-Enquiry-pdf")
async def sv2_extract(
    pdf: UploadFile = File(...),
) -> StreamingResponse:
    """Accept a PDF file and return an Excel workbook with extracted tags."""
    _validate_pdf(pdf)

    suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.TemporaryDirectory(prefix="poc-valves-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        pdf_path = tmpdir_path / f"upload{suffix}"
        pdf_bytes = await pdf.read()
        pdf_path.write_bytes(pdf_bytes)
        logger.info(
            "SV2 extract: received file %s -> %s", pdf.filename, pdf_path
        )

        try:
            text_result = extract_pdf_text(pdf_path)
            pages_meta = text_result.to_dict()["pages"]
            total_pages = len(pages_meta)
            logger.info(
                "Processing %d page(s) from %s", total_pages, pdf.filename
            )
            pages = []
            for p in pages_meta:
                pn = p["page_number"]
                logger.info(
                    "Extracting text & tables from page %d/%d",
                    pn,
                    total_pages,
                )
                try:
                    tables_obj = extract_pdf_tables_for_page(
                        pdf_path, pn, print_rows=False
                    )
                    tables = tables_obj.to_dict()["tables"]
                except Exception:
                    tables = []
                pages.append(
                    {
                        "page_number": pn,
                        "text": p["text"],
                        "image_b64": "",
                        "tables": tables,
                    }
                )

            total_text_chars = sum(len(p.get("text", "")) for p in pages)
            total_tables = sum(len(p.get("tables", [])) for p in pages)
            pipeline = SV2Pipeline()
            logger.info(
                "Sending %d page(s) with %d table(s) "
                "(%d total text chars) to LLM "
                "[model=%s, temperature=%s]",
                total_pages,
                total_tables,
                total_text_chars,
                pipeline.extractor.llm.model,
                pipeline.extractor.llm.temperature,
            )

            parsed = pipeline.run(pages, guide=None)

            logger.info(
                "LLM returned %d tag(s) from %s",
                len(parsed.tags),
                pdf.filename,
            )
        except Exception as exc:
            logger.exception("SV2 extraction failed")
            raise HTTPException(
                status_code=500, detail=str(exc)
            ) from exc

    excel_buf = ExcelWriter().build(parsed)
    output_filename = (
        Path(pdf.filename or "output").stem + "_tags_extracted.xlsx"
    )

    return StreamingResponse(
        excel_buf,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{output_filename}"'
            ),
        },
    )


def create_app() -> FastAPI:
    return app
