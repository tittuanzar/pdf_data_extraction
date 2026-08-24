#merges the config table with all the extraction results

from pathlib import Path
import pandas as pd

from poc_valves.pdf_extraction.services.accuracy_service import summarize_accuracy, _normalize_ext_id


OUTPUT_COLUMNS = [
    "Ext. ID",
    "Main Category",
    "Category",
    "Parameter / Field to Extract",
    "Extraction Guidance (AI instruction)",
    "Data Type",
    "Extracted Value",
    "Ground Truth Value",
    "Accuracy Match",
    "Accuracy Notes",
    "Source Clause",
    "Source Document",
    "Remarks / Validation"
]


def generate_output_excel(
    configuration_df: pd.DataFrame,
    extraction_results: list,
    output_path: str,
    accuracy_map: dict = None
):

    accuracy_map = accuracy_map or {}

    result_map = {
        result["ext_id"]: result
        for result in extraction_results
    }

    output_rows = []

    for _, row in configuration_df.iterrows():

        ext_id = str(row["Ext. ID"]).strip()

        result = result_map.get(ext_id, {})
        accuracy = accuracy_map.get(_normalize_ext_id(ext_id), {})

        output_rows.append({
            "Ext. ID": ext_id,
            "Main Category": row.get("Main Category"),
            "Category": row["Category"],
            "Parameter / Field to Extract":
                row["Parameter / Field to Extract"],
            "Extraction Guidance (AI instruction)":
                row["Extraction Guidance (AI instruction)"],

            "Data Type":
                row.get("Data Type"),

            "Extracted Value":
                result.get("extracted_value"),

            "Ground Truth Value":
                row.get("Ground Truth Value"),

            "Accuracy Match":
                accuracy.get("match_status"),

            "Accuracy Notes":
                accuracy.get("notes"),

            "Source Clause":
                result.get("source_clause"),

            "Source Document":
                result.get("source_document"),

            "Remarks / Validation":
                result.get("remarks_validation")
        })

    output_df = pd.DataFrame(
        output_rows,
        columns=OUTPUT_COLUMNS
    )

    Path(output_path).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:

        output_df.to_excel(
            writer,
            sheet_name="Extraction Results",
            index=False
        )

        if accuracy_map:

            summary = summarize_accuracy(accuracy_map)

            summary_df = pd.DataFrame([
                {"Metric": "Matches", "Value": summary["counts"]["match"]},
                {"Metric": "Partial Matches", "Value": summary["counts"]["partial_match"]},
                {"Metric": "Mismatches", "Value": summary["counts"]["mismatch"]},
                {"Metric": "Not Applicable (no ground truth)", "Value": summary["counts"]["not_applicable"]},
                #{"Metric": "Overall Accuracy %", "Value": summary["accuracy_pct"]},
                {"Metric": "Overall Accuracy %", "Value": f"{summary['accuracy_pct']:.1f}"},
            ])

            summary_df.to_excel(
                writer,
                sheet_name="Accuracy Summary",
                index=False
            )

    return output_path
