from __future__ import annotations

try:
    from fastapi import FastAPI, File, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
except Exception as exc:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "FastAPI is not installed. Install the 'api' extra to run the web application."
    ) from exc

from . import util

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
    util.log_startup_config()


@app.post("/extract-Tag-wise-Enquiry-pdf")
async def sv2_extract(
    pdf: UploadFile = File(...),
) -> StreamingResponse:
    """Accept a PDF file and return an Excel workbook with extracted tags."""
    excel_buf, output_filename = await util.run_sv2_extraction(pdf)
    return StreamingResponse(
        excel_buf,
        media_type=util.XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{output_filename}"',
        },
    )


@app.post("/upload-extraction-configuration")
async def upload_extraction_configuration(
    pdf: UploadFile = File(...),
) -> dict:
    """Upload the configuration PDF defining the fields to extract for
    the /extract-configured-pdf endpoint."""
    return await util.run_upload_extraction_configuration(pdf)


@app.post("/extract-configured-pdf")
async def extract_configured_pdf(
    pdf: UploadFile = File(...),
) -> StreamingResponse:
    """Accept a document PDF and return an Excel workbook extracted
    against the last uploaded configuration."""
    excel_buf, output_filename = await util.run_extract_configured_document(pdf)
    return StreamingResponse(
        excel_buf,
        media_type=util.XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{output_filename}"',
        },
    )


def create_app() -> FastAPI:
    return app
