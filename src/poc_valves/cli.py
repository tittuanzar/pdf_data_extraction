from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import PDFExtractionPipeline, PipelineConfig
from .schema import load_schema, validate_schema_contract
from .field_mapping_register.runner import run_pipeline as run_field_mapping_register


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="poc-valves")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-schema", help="Validate a schema file")
    validate_parser.add_argument("--schema", required=True)

    run_parser = subparsers.add_parser("run", help="Run the extraction pipeline")
    run_parser.add_argument("--pdf", required=True)
    run_parser.add_argument("--schema", required=True)
    run_parser.add_argument("--output-dir", required=True)
    run_parser.add_argument("--render-dpi", type=int, default=150)
    run_parser.add_argument("--max-workers", type=int, default=4)
    run_parser.add_argument("--excel", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")
    api_parser = subparsers.add_parser("api", help="Run the FastAPI app with uvicorn")
    api_parser.add_argument("--host", default="0.0.0.0")
    api_parser.add_argument("--port", type=int, default=8000)
    api_parser.add_argument("--reload", action="store_true")

    register_parser = subparsers.add_parser(
        "field-mapping-register",
        help="Extract the Field Mapping Register from a PDF and write Excel output",
    )
    register_parser.add_argument("--pdf", required=True, help="Input PDF file")
    register_parser.add_argument(
        "--output",
        default="field_mapping_register.xlsx",
        help="Output Excel file path",
    )
    register_parser.add_argument(
        "--pages-per-chunk",
        type=int,
        default=2,
        help="Number of PDF pages per Anthropic request",
    )
    register_parser.add_argument("--preview-only", action="store_true")
    register_parser.add_argument("--model", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate-schema":
        schema = load_schema(args.schema)
        validate_schema_contract(schema, require_complete=False)
        print(
            f"Loaded schema {schema.document_type} v{schema.version} "
            f"with {len(schema.fields)} fields across {len(schema.subcategories)} subcategories."
        )
        return 0

    if args.command == "run":
        schema = load_schema(args.schema)
        pipeline = PDFExtractionPipeline(schema)
        result = pipeline.run(
            args.pdf,
            PipelineConfig(
                output_dir=Path(args.output_dir),
                render_dpi=args.render_dpi,
                max_workers=args.max_workers,
                write_excel=args.excel,
                dry_run=args.dry_run,
            ),
        )
        print(f"Processed {result.document_name} with {len(result.fields)} extracted fields.")
        return 0

    if args.command == "api":
        try:
            import uvicorn
        except Exception as exc:  # pragma: no cover - optional dependency
            raise SystemExit("uvicorn is not installed. Install the 'api' extra first.") from exc

        uvicorn.run(
            "poc_valves.api:create_app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            factory=True,
        )
        return 0

    if args.command == "field-mapping-register":
        run_field_mapping_register(
            args.pdf,
            args.output,
            pages_per_chunk=args.pages_per_chunk,
            model=args.model,
            preview_only=args.preview_only,
        )
        return 0

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
