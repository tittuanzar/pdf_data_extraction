import os
import uuid
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from poc_valves.pdf_extraction.core.config import settings

from poc_valves.pdf_extraction.services.config_service import (
    load_configuration,
    load_configuration_dataframe,
    build_main_category_map,
    get_main_categories,
)
from poc_valves.pdf_extraction.services.pdf_service import extract_pdf_pages
from poc_valves.pdf_extraction.services.classification_service import classify_pages
from poc_valves.pdf_extraction.services.embedding_service import (
    create_embeddings,
    build_main_category_embedding_text,
    build_parameter_embedding_text,
    rank_pages_by_similarity,
)
from poc_valves.pdf_extraction.services.redis_service import (
    store_page,
    get_pages_for_category,
    delete_request_data,
)
from poc_valves.pdf_extraction.services.extraction_service import extract_category
from poc_valves.pdf_extraction.services.accuracy_service import evaluate_accuracy
from poc_valves.pdf_extraction.services.excel_service import generate_output_excel


class PdfExtractionPipeline:
    """
    Framework-agnostic entry point for the PDF extraction workflow.

    Wraps configuration upload, page classification, embedding-based
    retrieval, LLM extraction, accuracy scoring against the worked
    example, and Excel generation into two plain methods - so any
    front end (FastAPI routes, a CLI, a script, a notebook) can drive
    the pipeline without knowing about the individual service
    modules underneath.

    Usage:
        pipeline = PdfExtractionPipeline()
        pipeline.upload_configuration(config_pdf_bytes)
        output_path = pipeline.process_pdf(document_pdf_bytes)
    """

    def __init__(self, config_file: str = None):
        self.config_file = config_file or settings.CONFIG_FILE

    def has_configuration(self) -> bool:
        return os.path.exists(self.config_file)

    def upload_configuration(self, file_bytes: bytes) -> dict:
        """
        Saves the configuration PDF (the field-definition table) to
        disk and returns the main categories found in it.

        Raises ValueError if the PDF can't be parsed as a valid
        configuration table (missing required columns, no table
        found, etc).
        """

        config_path = Path(self.config_file)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "wb") as f:
            f.write(file_bytes)

        try:
            configuration = load_configuration(str(config_path))
        except Exception as exc:
            raise ValueError(f"Invalid configuration file: {exc}") from exc

        return {
            "message": "Configuration uploaded successfully.",
            "main_categories": get_main_categories(configuration),
        }

    def process_pdf(self, file_bytes: bytes) -> str:
        """
        Runs the full extraction + accuracy pipeline on an uploaded
        document PDF and returns the filesystem path to the
        generated Excel output file.

        Raises:
            ValueError - no configuration has been uploaded yet.
            RuntimeError - something failed during processing
                (classification, embedding, extraction, etc).
        """

        if not self.has_configuration():
            raise ValueError("Configuration PDF has not been uploaded.")

        request_id = str(uuid.uuid4())
        temp_pdf = None

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
                temp_pdf = temp.name
                temp.write(file_bytes)

            configuration = load_configuration(self.config_file)
            configuration_df = load_configuration_dataframe(self.config_file)
            main_category_map = build_main_category_map(configuration)

            pages = extract_pdf_pages(temp_pdf)

            classification = classify_pages(main_category_map, pages)
            classification_map = {
                item["page_number"]: item["categories"]
                for item in classification["pages"]
            }

            main_category_names = list(main_category_map.keys())
            main_category_texts = [
                build_main_category_embedding_text(name, main_category_map[name])
                for name in main_category_names
            ]
            main_category_embeddings = dict(zip(
                main_category_names,
                create_embeddings(main_category_texts)
            ))

            all_parameters = [
                parameter
                for parameters in configuration.values()
                for parameter in parameters
            ]
            parameter_texts = [
                build_parameter_embedding_text(parameter)
                for parameter in all_parameters
            ]
            parameter_embeddings = dict(zip(
                [p["ext_id"] for p in all_parameters],
                create_embeddings(parameter_texts)
            ))

            page_embeddings = create_embeddings(
                [page["content"] for page in pages]
            )

            for page, embedding in zip(pages, page_embeddings):
                page_number = page["page_number"]
                categories = classification_map.get(page_number, [])
                category_names = [item["category"] for item in categories]

                store_page(
                    request_id=request_id,
                    page_number=page_number,
                    content=page["content"],
                    categories=category_names,
                    embedding=embedding,
                )

            def _extract_for_category(main_category, sub_categories):
                category_pages = get_pages_for_category(
                    request_id,
                    main_category,
                    category_embedding=main_category_embeddings.get(main_category),
                )

                if not category_pages:
                    return []

                parameters = [
                    parameter
                    for parameters in sub_categories.values()
                    for parameter in parameters
                ]

                parameter_page_hints = {
                    parameter["ext_id"]: rank_pages_by_similarity(
                        parameter_embeddings[parameter["ext_id"]],
                        category_pages,
                        top_n=settings.PARAMETER_HINT_TOP_N,
                    )
                    for parameter in parameters
                    if parameter["ext_id"] in parameter_embeddings
                }

                result = extract_category(
                    category=main_category,
                    parameters=parameters,
                    pages=category_pages,
                    parameter_page_hints=parameter_page_hints,
                )

                return result.get("results", [])

            all_results = []

            # Each main category is an independent LLM call, so they're
            # run concurrently (bounded by LLM_MAX_CONCURRENCY) instead of
            # one after another - this is the dominant cost in the pipeline.
            with ThreadPoolExecutor(max_workers=settings.LLM_MAX_CONCURRENCY) as executor:
                futures = [
                    executor.submit(_extract_for_category, main_category, sub_categories)
                    for main_category, sub_categories in main_category_map.items()
                ]

                for future in as_completed(futures):
                    all_results.extend(future.result())

            accuracy_map = evaluate_accuracy(
                configuration_df=configuration_df,
                extraction_results=all_results,
            )

            output_file = f"output/{request_id}_extracted.xlsx"

            generate_output_excel(
                configuration_df=configuration_df,
                extraction_results=all_results,
                output_path=output_file,
                accuracy_map=accuracy_map,
            )

            return output_file

        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        finally:
            try:
                delete_request_data(request_id)
            except Exception:
                pass

            if temp_pdf and os.path.exists(temp_pdf):
                os.remove(temp_pdf)
