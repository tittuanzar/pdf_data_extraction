from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
except Exception as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "FastAPI is not installed. Install the 'api' extra to run the web application."
    ) from exc

from .pdf.pdf_text import PdfTextExtractionError, extract_pdf_text, extract_pdf_tables_for_page
import re
from .pdf.tagged_products import extract_tagged_products
import json
from .pipeline.sv2_pipeline import load_field_registry, run_pipeline_sv2, extract_sv2_output_from_pages
from .pydantic_output import ParsedOutput
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


app = FastAPI(title="Poc Valves PDF Text API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _extract_pdf_text(pdf: UploadFile) -> dict[str, Any]:
    suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.TemporaryDirectory(prefix="poc-valves-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        pdf_path = tmpdir_path / f"upload{suffix}"
        pdf_bytes = await pdf.read()
        pdf_path.write_bytes(pdf_bytes)

        try:
            result = extract_pdf_text(pdf_path)
        except PdfTextExtractionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result.to_dict()


async def _extract_tagged_products(pdf: UploadFile) -> dict[str, Any]:
    suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.TemporaryDirectory(prefix="poc-valves-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        pdf_path = tmpdir_path / f"upload{suffix}"
        pdf_bytes = await pdf.read()
        pdf_path.write_bytes(pdf_bytes)

        try:
            result = extract_tagged_products(pdf_path)
        except PdfTextExtractionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result.to_dict()


@app.post("/sv2-extract", response_model=ParsedOutput)
async def sv2_extract(
    pdf: UploadFile = File(...),
    evidence: str | None = Form(None),
    debug: bool = False,
) -> ParsedOutput:
        """SV2 extraction endpoint. Accepts the PDF and an optional `evidence`
        form field containing JSON with `pages` (list of {page_number, image_b64,
        text, tables}) and an optional `guide` string. The final response must
        validate against ParsedOutput.
        """
        suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
        with tempfile.TemporaryDirectory(prefix="poc-valves-") as tmpdir:
            tmpdir_path = Path(tmpdir)
            pdf_path = tmpdir_path / f"upload{suffix}"
            pdf_bytes = await pdf.read()
            pdf_path.write_bytes(pdf_bytes)
            logger.info("SV2 extract: received file %s -> %s", pdf.filename, pdf_path)

            try:
                if evidence:
                    payload = json.loads(evidence)
                    pages = payload.get("pages", [])
                    guide = payload.get("guide")
                else:
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
                    guide = None

                parsed = extract_sv2_output_from_pages(pages, guide=guide, debug=debug)
                return parsed
            except Exception as exc:
                logging.getLogger(__name__).exception("SV2 extraction failed")
                raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/extract")
async def extract(
    pdf: UploadFile = File(...),
) -> dict[str, Any]:
    return await _extract_pdf_text(pdf)


@app.post("/extract-tagged-products")
async def extract_tagged_products_endpoint(
    pdf: UploadFile = File(...),
) -> dict[str, Any]:
    return await _extract_tagged_products(pdf)


def create_app() -> FastAPI:
    return app
