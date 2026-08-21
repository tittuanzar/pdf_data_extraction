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
   ┌──────────────────┐ ┌────────────────────────────┐ ┌────────────────────┐
   │  PDF EXTRACTION  │ │   LLM PIPELINE (3-phase)   │ │  EXCEL GENERATION  │
   │   (pdfplumber)   │ │  (OpenAI GPT → tags list)  │ │     (openpyxl)     │
   └─────────┬────────┘ └──────────────┬─────────────┘ └──────────┬─────────┘
             │                         │                          │
             ▼                         ▼                          ▼
      Pages + Tables ParsedOutput.tags: [Tag1, Tag2, ...]  .xlsx download
        (per page)    (LLM parses grouped pages into one  (per-tag sheets)
                       SV2ValveDatasheet per tag found)
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

**Source:** `src/poc_valves/api.py:46-64` (`_validate_pdf`)

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

**Source:** `src/poc_valves/core/pdf/parser.py` (`PdfParser` class)  
**Called from:** `src/poc_valves/api.py:85-113`

---

### Stage 3 — Three-Phase LLM Extraction (OpenAI, structured output)

`SV2Pipeline.run(pages)` groups pages into per-tag page groups first (a
tag's "main" datasheet page plus its continuation page(s), matched by
regex), then runs three phases per tag group:

```
pages (list of dicts)
    │
    ▼
_group_pages_by_tag(pages) ──► [[main, continuation, ...], ...]  (one group per tag)
    │
    ▼  for each tag group: _merge_page_group() → single merged page dict
    │
    ├─ Phase 1 — SV2FieldExtractor.extract_page(page)
    │     LLM call, response_format=PageExtraction
    │     → direct_values (66-field attrs) + aux_values (raw_* enquiry values)
    │
    ├─ Phase 2 — derived fields
    │     2a. postprocess.apply_pre_llm_derivations()   deterministic rules
    │     2b. SV2FieldExtractor.derive_judgment_fields() LLM call, response_format=DerivedJudgmentFields
    │         (11 judgment-based fields: positioner selection, MDMT, IBR, ...)
    │     2c. postprocess.apply_post_llm_derivations()  deterministic rules depending on 2b's output
    │
    ├─ Phase 3 — postprocess.apply_engineered_defaults()
    │     Org-standard defaults for fields never present in the enquiry PDF
    │     (painting scheme, cable gland type, positioner type, bench range, guiding)
    │
    └─ _finalize_tag() → SV2ValveDatasheet (validates; missing required
                          fields become "NOT EXTRACTED" placeholders, logged)
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

**Source:** `src/poc_valves/core/pipeline/sv2_pipeline.py` (`SV2Pipeline.run`, page grouping/merging) + `src/poc_valves/core/llm/extractor.py` (`SV2FieldExtractor`, the two LLM calls) + `src/poc_valves/core/pipeline/postprocess.py` (deterministic derivation/defaults)  
**Model:** `src/poc_valves/core/models.py` (66 fields per tag)

---

### Stage 4 — Excel Workbook Generation

```
ParsedOutput
    │
    ▼
ExcelWriter().build(parsed)
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

**Source:** `src/poc_valves/core/excel.py` (`ExcelWriter.build`), called from `src/poc_valves/api.py:142`

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
  │                   ┌─────┴──────────┐                   │
  │                   │ SV2Pipeline    │                   │
  │                   │  .run(pages)   │                   │
  │                   │ (3 phases,     │                   │
  │                   │  per tag group)│                   │
  │                   └─────┬──────────┘  pages[] per phase │
  │                         │ ───────────────────────────► │
  │                         │         OpenAI GPT-4o        │
  │                         │   (structured output)        │
  │                         │ ◄─────────────────────────── │
  │                         │        ParsedOutput          │
  │                   ┌─────┴──────────┐                   │
  │                   │ openpyxl       │                   │
  │                   │ ExcelWriter    │                   │
  │                   │  .build()      │                   │
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
├── api.py                          FastAPI endpoint — routes only, delegates to core/
│
├── core/
│   ├── config.py                   Settings loader (class _Settings) + prompt loading
│   ├── models.py                   SV2ValveDatasheet (66 fields) + ParsedOutput + PartialSV2ValveDatasheet
│   ├── excel.py                    ExcelWriter — builds the per-tag workbook
│   │
│   ├── pdf/
│   │   └── parser.py               PdfParser — pdfplumber text & table extraction
│   │
│   ├── llm/
│   │   ├── client.py               LLMClient — OpenAI client + chat_parse() (usage/cost accounting)
│   │   └── extractor.py            SV2FieldExtractor — Phase 1 direct-field + Phase 2 judgment-field LLM calls
│   │
│   └── pipeline/
│       ├── sv2_pipeline.py         SV2Pipeline — three-phase orchestration (page grouping, phases 1-3)
│       └── postprocess.py          Deterministic derive_* rules + engineered defaults
│
└── field_mapping_register/
    ├── models.py                   Field-mapping dataclasses
    ├── extract_pdf_text.py         PDF text extraction (field-mapping)
    ├── llm_extract_rows.py         OpenAI row extraction (gpt-4.1)
    ├── build_excel.py              openpyxl writer for field-mapping
    └── runner.py                   CLI runner (extract → merge → Excel) — standalone tool, not used by api.py
```

`field_mapping_register/` is a separate, self-contained CLI utility unrelated to the FastAPI endpoint above; it has its own tests and is not part of the request lifecycle described in this document.

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
