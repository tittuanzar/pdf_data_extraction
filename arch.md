# Architecture — POC Valves Extraction Pipeline

## High-Level Data Flow

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT (Browser / cURL)                         │
│                                                                              │
│   POST /extract-Tag-wise-Enquiry-pdf                                         │
│   Content-Type: multipart/form-data                                          │
│   Body: pdf=<file>                                                           │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          FASTAPI ENDPOINT (api.py)                           │
│                                                                              │
│  1. _validate_pdf()  ──  Reject non-.pdf extension / content-type            │
│  2. Write bytes to temp file                                                 │
│  3. Orchestrate pipeline stages (below)                                      │
│  4. Build Excel → StreamingResponse                                          │
└────────────────────────────────┬─────────────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                   ▼
   ┌─────────────────┐ ┌────────────────┐ ┌────────────────────┐
   │  PDF EXTRACTION  │ │  LLM PIPELINE  │ │  EXCEL GENERATION  │
   │   (pdfplumber)   │ │  (OpenAI GPT)  │ │    (openpyxl)      │
   └────────┬────────┘ └───────┬────────┘ └─────────┬──────────┘
            │                  │                     │
            ▼                  ▼                     ▼
    Pages + Tables     ParsedOutput            .xlsx download
    (per page)         (list of tags)          (per-tag sheets)
```

---

## Stage-by-Stage Detail

### Stage 1 — PDF Ingestion & Validation

```
UploadFile (pdf)
       │
       ▼
┌──────────────────────────┐
│  _validate_pdf(pdf)      │
│                          │
│  • Check extension == .pdf
│  • Check content-type    │
│    contains "pdf"        │
│                          │
│  Fail → HTTP 400         │
└──────────────┬───────────┘
               │
               ▼
      tmpdir/upload.pdf     (bytes written to temp file)
```

**Source:** `src/poc_valves/api.py:37-51`

---

### Stage 2 — PDF Text & Table Extraction (pdfplumber)

```
upload.pdf
    │
    ├──► extract_pdf_text(pdf_path)
    │         │
    │         ▼
    │    PdfTextResult
    │    ┌────────────────────────────────┐
    │    │ filename: str                   │
    │    │ page_count: int                 │
    │    │ pages: [PdfPageText, ...]       │
    │    │   └─ page_number: int           │
    │    │      text: str                  │
    │    └────────────────────────────────┘
    │
    └──► extract_pdf_tables_for_page(pdf_path, page_number)
              │  (called per page)
              ▼
         PdfPageTables
         ┌────────────────────────────────┐
         │ page_number: int               │
         │ tables: list[list[list[str]]]  │
         └────────────────────────────────┘

    Merge into:
    ┌──────────────────────────────────────────────────┐
    │  pages: [                                        │
    │    {                                              │
    │      "page_number": 1,                            │
    │      "text": "...",                               │
    │      "image_b64": "",                             │
    │      "tables": [[["row1col1","row1col2"],...]]    │
    │    },                                             │
    │    ...                                            │
    │  ]                                                │
    └──────────────────────────────────────────────────┘
```

**Source:** `src/poc_valves/pdf/pdf_text.py`  
**Called from:** `src/poc_valves/api.py:94-104`

---

### Stage 3 — LLM Structured Extraction (OpenAI GPT-4o)

```
pages (list of dicts)
    │
    ▼
extract_sv2_output_from_pages(pages)
    │
    │  Builds OpenAI request:
    │    • System prompt: "You extract a valve datasheet..."
    │    • User content: JSON dump of all pages (text + tables)
    │    • response_format = ParsedOutput (structured output)
    │
    ▼
┌──────────────────────────────────────────────────────┐
│              OpenAI GPT-4o API                       │
│                                                      │
│  Input:  System msg + Pages JSON                     │
│  Output: ParsedOutput (Pydantic structured response) │
└──────────────────────────┬───────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────┐
│                   ParsedOutput                       │
│                                                      │
│  {                                                   │
│    "tags": [                                         │
│      SV2ValveDatasheet {          // Tag 1           │
│        tag_no: "FV-1001",                            │
│        service: "...",                               │
│        fluid_name: "...",                            │
│        ... (66 fields total)                         │
│        peso_certificate: true/false/null             │
│      },                                              │
│      SV2ValveDatasheet {          // Tag 2           │
│        ...                                           │
│      }                                               │
│    ]                                                 │
│  }                                                   │
└──────────────────────────────────────────────────────┘
```

**Source:** `src/poc_valves/pipeline/sv2_pipeline.py:160-216`  
**Model:** `src/poc_valves/pydantic_output.py` (66 fields per tag)

---

### Stage 4 — Excel Workbook Generation

```
ParsedOutput
    │
    ▼
_build_excel(parsed)
    │
    │  For each tag in parsed.tags:
    │    • Create sheet named after tag_no (max 31 chars)
    │    • Write header: Sl.No | Feature name | Extracted feature value
    │    • Write 66 rows (one per SV2ValveDatasheet field)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  .xlsx Workbook                                         │
│                                                         │
│  ┌─────────────────────┐  ┌─────────────────────┐      │
│  │ Sheet: "FV-1001"    │  │ Sheet: "FV-1002"    │ ...  │
│  │                     │  │                     │      │
│  │ Sl.No│Feature│Value │  │ Sl.No│Feature│Value │      │
│  │  1    │tag_no │..   │  │  1    │tag_no │..   │      │
│  │  2    │service│..   │  │  2    │service│..   │      │
│  │  ...  │ ...   │..   │  │  ...  │ ...   │..   │      │
│  │  66   │peso_..│..   │  │  66   │peso_..│..   │      │
│  └─────────────────────┘  └─────────────────────┘      │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
               StreamingResponse
         Content-Type: application/vnd...
         Content-Disposition: attachment; filename="file_tags_extracted.xlsx"
```

**Source:** `src/poc_valves/api.py:54-77`

---

## Complete Request Lifecycle

```
Client                    Server                        External
  │                         │                              │
  │  POST /extract-Tag-…    │                              │
  │  multipart/form-data    │                              │
  │  pdf=enquiry.pdf        │                              │
  │ ──────────────────────► │                              │
  │                         │                              │
  │                   ┌─────┴──────┐                       │
  │                   │ Validate   │                       │
  │                   │ PDF only   │                       │
  │                   └─────┬──────┘                       │
  │                         │                              │
  │                   ┌─────┴──────┐                       │
  │                   │ Write to   │                       │
  │                   │ temp file  │                       │
  │                   └─────┬──────┘                       │
  │                         │                              │
  │                   ┌─────┴──────────┐                   │
  │                   │ pdfplumber     │                   │
  │                   │ extract_text() │                   │
  │                   │ extract_tables │                   │
  │                   └─────┬──────────┘                   │
  │                         │                              │
  │                         │  pages[]                     │
  │                         │ ───────────────────────────► │
  │                         │         OpenAI GPT-4o        │
  │                         │   (structured output)        │
  │                         │ ◄─────────────────────────── │
  │                         │        ParsedOutput          │
  │                   ┌─────┴──────────┐                   │
  │                   │ openpyxl       │                   │
  │                   │ _build_excel() │                   │
  │                   └─────┬──────────┘                   │
  │                         │                              │
  │  StreamingResponse      │                              │
  │  ← .xlsx download       │                              │
  │ ◄────────────────────── │                              │
```

---

## Module Map

```
src/poc_valves/
├── api.py                     FastAPI endpoint + Excel builder
├── pydantic_output.py         SV2ValveDatasheet (66 fields) + ParsedOutput
├── export.py                  Generic JSON/CSV/XLSX exporters
├── models.py                  Dataclass models (DocumentResult, etc.)
│
├── pipeline/
│   ├── sv2_pipeline.py        LLM extraction (GPT-4o structured output)
│   ├── pipeline.py            Generic schema-driven pipeline (unused)
│   ├── llm.py                 ExtractionClient ABC + mock
│   ├── prompts.py             Prompt builders
│   ├── service.py             Heuristic regex extraction
│   └── validation.py          Type coercion + validation
│
├── pdf/
│   ├── pdf_text.py            pdfplumber text & table extraction
│   └── preprocess.py          PyMuPDF + pdfplumber preprocessing
│
├── schema/
│   ├── schema.py              Schema loading & validation
│   └── output_model.py        Dynamic Pydantic model from Excel
│
└── field_mapping_register/
    ├── models.py              Field-mapping dataclasses
    ├── extract_pdf_text.py    PDF text extraction (field-mapping)
    ├── llm_extract_rows.py    OpenAI row extraction (gpt-4.1)
    ├── build_excel.py         openpyxl writer for field-mapping
    └── runner.py              CLI runner (extract → merge → Excel)
```

---

## Key Dependencies

| Component        | Library          | Purpose                              |
|------------------|------------------|--------------------------------------|
| API Framework    | FastAPI          | HTTP endpoint + multipart upload     |
| PDF Parsing      | pdfplumber       | Text & table extraction from PDF     |
| LLM Extraction   | openai (GPT-4o)  | Structured output for 66-field model |
| Excel Output     | openpyxl         | Workbook generation (.xlsx)          |
| Data Models      | pydantic         | Typed schemas (SV2ValveDatasheet)    |
| ASGI Server      | uvicorn          | Production server                    |
