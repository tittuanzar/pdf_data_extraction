"""Supporting functions for the API routes in :mod:`poc_valves.api`.

All request validation, business logic, and pipeline orchestration for
the routes lives here, so ``api.py`` only has to declare routes and
build HTTP responses.
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .core.excel import ExcelWriter
from .core.pdf import extract_pdf_tables_for_page, extract_pdf_text
from .core.pipeline import SV2Pipeline
from .pdf_extraction.services.pipeline_service import PdfExtractionPipeline

logger = logging.getLogger(__name__)

ALLOWED_MIME = "application/pdf"
ALLOWED_EXTENSIONS = {".pdf"}
XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def log_startup_config() -> None:
    """Log the LLM model/temperature the SV2 pipeline will use."""
    pipeline = SV2Pipeline()
    logger.info(
        "Startup config — model: %s, temperature: %s",
        pipeline.extractor.llm.model,
        pipeline.extractor.llm.temperature,
    )


def validate_pdf_upload(pdf: UploadFile) -> None:
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


async def run_sv2_extraction(pdf: UploadFile) -> tuple[io.BytesIO, str]:
    """Run the tag-wise SV2 extraction pipeline on an uploaded PDF.

    Returns the generated Excel workbook as an in-memory buffer along
    with the output filename.
    """
    validate_pdf_upload(pdf)

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
    return excel_buf, output_filename


async def run_upload_extraction_configuration(pdf: UploadFile) -> dict:
    """Upload the configuration PDF that defines the extraction schema
    used by :func:`run_extract_configured_document`.
    """
    validate_pdf_upload(pdf)
    pdf_bytes = await pdf.read()
    logger.info("Extraction config upload: received file %s", pdf.filename)

    try:
        result = PdfExtractionPipeline().upload_configuration(pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Extraction configuration upload failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    logger.info(
        "Extraction config uploaded: %d main categor(y/ies)",
        len(result.get("main_categories", [])),
    )
    return result


async def run_extract_configured_document(
    pdf: UploadFile,
) -> tuple[io.BytesIO, str]:
    """Run the configured-extraction pipeline on an uploaded document
    PDF, against whatever configuration was last uploaded via
    :func:`run_upload_extraction_configuration`.

    Returns the generated Excel workbook as an in-memory buffer along
    with the output filename.
    """
    validate_pdf_upload(pdf)
    pdf_bytes = await pdf.read()
    logger.info("Extraction process: received file %s", pdf.filename)

    try:
        output_path = PdfExtractionPipeline().process_pdf(pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("Configured PDF extraction failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    output_file = Path(output_path)
    excel_buf = io.BytesIO(output_file.read_bytes())
    output_file.unlink(missing_ok=True)

    output_filename = Path(pdf.filename or "output").stem + "_extracted.xlsx"
    return excel_buf, output_filename
