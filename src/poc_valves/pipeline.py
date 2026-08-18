from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .export import write_results_csv, write_results_json, write_results_xlsx
from .llm import ExtractionClient, MockExtractionClient, parse_json_response
from .models import DocumentResult, ExtractedField, SchemaDefinition
from .preprocess import PreprocessResult, preprocess_pdf
from .prompts import build_subcategory_prompt
from .schema import all_subcategories, group_fields_by_subcategory
from .validation import validate_result


@dataclass(slots=True)
class PipelineConfig:
    output_dir: Path
    render_dpi: int = 150
    max_workers: int = 4
    write_excel: bool = False
    write_csv: bool = True
    dry_run: bool = False


class PDFExtractionPipeline:
    def __init__(self, schema: SchemaDefinition, client: ExtractionClient | None = None):
        self.schema = schema
        self.client = client or MockExtractionClient()

    def run(self, pdf_path: str | Path, config: PipelineConfig) -> DocumentResult:
        pdf_path = Path(pdf_path)
        config.output_dir.mkdir(parents=True, exist_ok=True)

        preprocessed = preprocess_pdf(pdf_path, config.output_dir, dpi=config.render_dpi)
        document_text = preprocessed.assembled_text()

        grouped_fields = group_fields_by_subcategory(self.schema)
        subcategories = all_subcategories(self.schema)
        if not subcategories:
            subcategories = sorted(grouped_fields)

        raw_responses: dict[str, Any] = {}
        extracted_fields: dict[str, ExtractedField] = {}

        if not config.dry_run:
            with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
                futures = {}
                for subcategory in subcategories:
                    fields = grouped_fields.get(subcategory, [])
                    if not fields:
                        continue
                    prompt = build_subcategory_prompt(self.schema, subcategory, fields, document_text)
                    futures[executor.submit(self.client.extract, prompt, images=self._image_paths_for_subcategory(fields, preprocessed))] = subcategory

                for future in as_completed(futures):
                    subcategory = futures[future]
                    raw = future.result()
                    raw_responses[subcategory] = raw
                    payload = parse_json_response(raw)
                    for item in payload.get("fields", []):
                        field_id = str(item.get("field_id"))
                        extracted_fields[field_id] = ExtractedField(
                            field_id=field_id,
                            value=item.get("value"),
                            source_page=item.get("source_page"),
                            confidence=item.get("confidence"),
                            notes=item.get("notes"),
                        )

        result = DocumentResult(
            document_name=pdf_path.name,
            metadata={
                "source_pdf": pdf_path.as_posix(),
                "render_dpi": config.render_dpi,
                "dry_run": config.dry_run,
            },
            fields=extracted_fields,
            validation=[],
            raw_responses=raw_responses,
            page_artifacts=preprocessed.pages,
        )
        validated = validate_result(self.schema, result, preprocessed.pages)

        write_results_json(config.output_dir / "results.json", validated)
        write_results_csv(config.output_dir / "results.csv", self.schema, validated)
        if config.write_excel:
            write_results_xlsx(config.output_dir / "results.xlsx", self.schema, validated)
        return validated

    def _image_paths_for_subcategory(
        self, fields: list[Any], preprocessed: PreprocessResult
    ) -> list[str]:
        # The schema can later add per-field page targeting. For now we pass all pages.
        return [page.image_path for page in preprocessed.pages if page.image_path]
