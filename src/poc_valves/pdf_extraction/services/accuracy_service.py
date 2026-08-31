import json
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Optional

from openai import OpenAI

from poc_valves.pdf_extraction.core.config import settings
from poc_valves.pdf_extraction.prompts.accuracy_prompt import build_accuracy_prompt
from poc_valves.pdf_extraction.services.usage_service import UsageTracker


client = OpenAI(api_key=settings.OPENAI_API_KEY)

logger = logging.getLogger(__name__)


def _normalize_ext_id(value) -> str:
    """
    Normalizes an Ext. ID for matching between the configuration
    table, the extraction results, and the LLM's accuracy-comparison
    response - strips whitespace and lowercases, so trivial
    formatting differences (extra spaces, case) don't cause a
    comparison to silently fail to line up.
    """
    return str(value or "").strip().lower()


def _chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _evaluate_batch(batch: list, tracker: Optional[UsageTracker] = None):

    prompt = build_accuracy_prompt(batch)

    response = client.chat.completions.create(
        model=settings.OPENAI_CHAT_MODEL,
        temperature=0,
        response_format={
            "type": "json_object"
        },
        messages=[
            {
                "role": "system",
                "content": (
                    "You validate extracted field values against a "
                    "known-correct worked example."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    if tracker is not None:
        tracker.add_chat_usage(response.usage, label="Accuracy batch")

    content = json.loads(response.choices[0].message.content)

    comparisons = content.get("comparisons", [])

    logger.info(
        "Accuracy batch: sent %d items, got %d comparisons back",
        len(batch),
        len(comparisons),
    )

    return {
        _normalize_ext_id(item.get("ext_id")): {
            "match_status": item.get("match_status"),
            "notes": item.get("notes"),
        }
        for item in comparisons
    }


def evaluate_accuracy(
    configuration_df,
    extraction_results: list,
    batch_size: int = None,
    tracker: Optional[UsageTracker] = None,
):
    """
    Compares each row's ground-truth value (from the "Ground Truth Value"
    column, if present in the uploaded config PDF) against the pipeline's
    extracted value for the same Ext. ID, using an LLM to judge semantic
    equivalence rather than exact string matching.

    Returns { normalized_ext_id: { "match_status": ..., "notes": ... } }.
    Rows with no ground-truth value available are skipped (not scored).

    Keys are normalized (stripped + lowercased) via _normalize_ext_id so
    callers must use the same normalization when looking values up -
    see excel_service.generate_output_excel.
    """

    if "Ground Truth Value" not in configuration_df.columns:
        logger.warning(
            "evaluate_accuracy: 'Ground Truth Value' column not found "
            "in configuration_df. Columns present: %s",
            list(configuration_df.columns),
        )
        return {}

    batch_size = batch_size or settings.ACCURACY_BATCH_SIZE

    extracted_map = {
        _normalize_ext_id(result.get("ext_id")): result.get("extracted_value")
        for result in extraction_results
    }

    comparisons = []

    for _, row in configuration_df.iterrows():

        ground_truth = row.get("Ground Truth Value")

        if not ground_truth or not str(ground_truth).strip():
            continue

        ext_id_raw = str(row["Ext. ID"]).strip()

        comparisons.append({
            "ext_id": ext_id_raw,
            "parameter": row["Parameter / Field to Extract"],
            "data_type": row.get("Data Type"),
            "extracted_value": extracted_map.get(_normalize_ext_id(ext_id_raw)),
            "ground_truth_value": ground_truth,
        })

    logger.info(
        "evaluate_accuracy: %d rows have a ground truth value out of %d total rows",
        len(comparisons),
        len(configuration_df),
    )

    if not comparisons:
        return {}

    accuracy_map = {}
    batches = list(_chunk(comparisons, batch_size))

    # Batches are scored independently, so they're run concurrently
    # (bounded by LLM_MAX_CONCURRENCY) instead of one after another.
    with ThreadPoolExecutor(max_workers=settings.LLM_MAX_CONCURRENCY) as executor:
        for result in executor.map(
            partial(_evaluate_batch, tracker=tracker), batches
        ):
            accuracy_map.update(result)

    logger.info(
        "evaluate_accuracy: final accuracy_map has %d entries", len(accuracy_map)
    )

    return accuracy_map


def summarize_accuracy(accuracy_map: dict):
    """
    Aggregates match_status counts and an overall accuracy percentage
    (match / (match + partial_match + mismatch), excluding
    not_applicable rows).
    """

    counts = {"match": 0, "partial_match": 0, "mismatch": 0, "not_applicable": 0}

    for item in accuracy_map.values():
        status = item.get("match_status")
        if status in counts:
            counts[status] += 1

    scored_total = counts["match"] + counts["partial_match"] + counts["mismatch"]

    accuracy_pct = (
        round(100 * counts["match"] / scored_total, 1)
        if scored_total else None
    )

    return {
        "counts": counts,
        "scored_total": scored_total,
        "accuracy_pct": accuracy_pct,
    }
