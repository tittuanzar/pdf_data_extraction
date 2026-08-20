# This file deals with the document containing the table, it uses pdfplumber to extract the table and work on it.

import re
from pathlib import Path

import pandas as pd
import pdfplumber


REQUIRED_COLUMN_PREFIXES = {
    "Ext. ID": "Ext. ID",
    "Category": "Category",
    "Parameter / Field to Extract": "Parameter / Field to Extract",
    "Extraction Guidance": "Extraction Guidance (AI instruction)",
    "Typical Clause Keywords": "Typical Clause Keywords",
    "Data Type": "Data Type",
    "Unit / Allowed Values": "Unit / Allowed Values",
    "Priority": "Priority",
    "Target MIL DS Field": "Target MIL DS Field",
}

# Optional - not all configs will have a worked-example / ground-truth
# column, so this is matched separately (by substring, not strict
# startswith) and never validated as required. Matching is deliberately
# loose because this header sometimes gets extracted by pdfplumber as a
# single cell ("Extracted Value (Worked Example - Dangote DRPP)") and
# sometimes as two adjacent cells ("Extracted Value" | "(Worked Example
# - Dangote DRPP)") depending on how the PDF laid out the header row.
GROUND_TRUTH_KEYWORDS = ("worked example", "extracted value")

SECTION_HEADER_PATTERN = re.compile(r"^\d+\.\s*[A-Z]")

NON_MERGEABLE_COLUMNS = {"Main Category"}

UNCATEGORIZED_MAIN_CATEGORY = "Uncategorized"


def _clean_cell(cell):
    if cell is None:
        return ""
    return " ".join(str(cell).split())


def _looks_like_header(row, header) -> bool:
    """
    True if `row` is a repeated header row (appears again on a
    later page of a multi-page table).
    """

    if len(row) != len(header):
        return False

    matches = sum(
        1
        for a, b in zip(row, header)
        if _clean_cell(a) == _clean_cell(b)
    )

    return matches >= len(header) * 0.7


def _extract_section_header(row):
    """
    Detects a full-width section-divider row and returns its text,
    e.g. "1. DOCUMENT & PROJECT IDENTIFICATION". Works whether the
    row was extracted with exactly one populated cell (rest blank)
    or with a different cell count than the table header - both
    shapes are checked before any header-length filtering happens.
    """

    non_empty = [cell.strip() for cell in row if cell and cell.strip()]

    if len(non_empty) == 1 and SECTION_HEADER_PATTERN.match(non_empty[0]):
        return non_empty[0]

    return None


def _merge_split_ground_truth_header(header: list) -> list:
    """
    Handles the case where pdfplumber splits the ground-truth header
    into two adjacent cells, e.g.:
        [..., "Extracted Value", "(Worked Example - Dangote DRPP)", ...]
    instead of one cell:
        [..., "Extracted Value (Worked Example - Dangote DRPP)", ...]

    If found, merges the pair into the first cell and blanks the
    second, so the header ends up as a single logical column before
    any prefix/keyword matching happens. Leaves the header untouched
    if no such split pair exists.
    """

    merged = list(header)

    for i in range(len(merged) - 1):

        current = (merged[i] or "").strip().lower()
        nxt = (merged[i + 1] or "").strip().lower()

        if current.startswith("extracted value") and "worked example" in nxt:
            merged[i] = f"{merged[i]} {merged[i + 1]}".strip()
            merged[i + 1] = ""
            break

    return merged


def _drop_blank_header_columns(header: list, rows: list):
    """
    Drops any header column left blank (e.g. after
    _merge_split_ground_truth_header emptied the second half of a
    split pair) along with the corresponding cell in every data row,
    so the header and row lengths still match downstream.
    """

    keep_indices = [i for i, col in enumerate(header) if col and col.strip()]

    if len(keep_indices) == len(header):
        return header, rows

    new_header = [header[i] for i in keep_indices]

    new_rows = []
    for row in rows:
        if len(row) != len(header):
            # Section-header rows / malformed rows: leave as-is,
            # downstream length checks will filter them out.
            new_rows.append(row)
            continue
        new_rows.append([row[i] for i in keep_indices])

    return new_header, new_rows


def extract_table_from_pdf(file_path: str) -> pd.DataFrame:
    """
    Extracts the configuration table from a PDF document.

    Handles:
    - tables spanning multiple pages, with or without a repeated
      header row
    - full-width section divider rows (e.g. "1. DOCUMENT & PROJECT
      IDENTIFICATION"), captured as a "Main Category" that is
      carried forward onto every subsequent data row until the next
      section header appears
    - stray non-table content (titles, notes) picked up as spurious
      low-column-count "tables"
    - a ground-truth / worked-example header that pdfplumber may
      split across two adjacent cells
    """

    header = None
    header_len = None
    all_rows = []
    main_categories = []
    current_main_category = None

    with pdfplumber.open(file_path) as pdf:

        for page in pdf.pages:

            tables = page.extract_tables()

            for table in tables:

                if not table:
                    continue

                cleaned_table = [
                    [_clean_cell(cell) for cell in row]
                    for row in table
                ]

                if header is None:
                    if len(cleaned_table[0]) < 5:
                        continue

                    header = _merge_split_ground_truth_header(cleaned_table[0])
                    header_len = len(header)
                    rows = cleaned_table[1:]
                else:
                    first_row = cleaned_table[0]

                    if _looks_like_header(first_row, header):
                        rows = cleaned_table[1:]
                    else:
                        rows = cleaned_table

                for row in rows:

                    section_header = _extract_section_header(row)

                    if section_header:
                        current_main_category = section_header
                        continue

                    if len(row) != header_len:
                        continue

                    # Drop fully blank rows
                    if not any(cell.strip() for cell in row):
                        continue

                    all_rows.append(row)
                    main_categories.append(current_main_category)

    if header is None:
        raise ValueError("No table found in the PDF.")

    header, all_rows = _drop_blank_header_columns(header, all_rows)

    df = pd.DataFrame(all_rows, columns=header)
    df["Main Category"] = main_categories

    df = _map_required_columns(df)
    df = merge_wrapped_rows(df)

    return df


def _map_required_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Renames whichever actual header columns match our required field
    prefixes to their canonical names, so downstream code can rely
    on fixed column names regardless of document-specific suffixes
    (e.g. "Target MIL DS Field (MIL-FM-07-040)").

    Also detects the optional ground-truth / worked-example column
    by keyword match (not strict prefix), since its exact wording
    varies per document (e.g. "Extracted Value (Worked Example -
    Dangote DRPP)").
    """

    rename_map = {}

    for actual_col in df.columns:

        normalized = actual_col.strip().lower()

        matched = False

        for prefix, canonical in REQUIRED_COLUMN_PREFIXES.items():
            if normalized.startswith(prefix.lower()):
                rename_map[actual_col] = canonical
                matched = True
                break

        if matched:
            continue

        if any(keyword in normalized for keyword in GROUND_TRUTH_KEYWORDS):
            # Guard against accidentally matching a genuinely
            # different "Extracted Value" style column - require it
            # to look like the worked-example one specifically.
            if "worked example" in normalized or "example" in normalized:
                rename_map[actual_col] = "Ground Truth Value"

    return df.rename(columns=rename_map)


def merge_wrapped_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    If a row has no Ext. ID, treat it as a continuation of the
    previous row (text wrapped across a page break) and merge its
    cell content into the previous row instead of keeping it as a
    separate record. "Main Category" is a fixed per-row tag, not
    wrapped free text, so it's left as-is rather than appended.
    """

    if "Ext. ID" not in df.columns:
        return df

    merged_rows = []

    for _, row in df.iterrows():

        ext_id = str(row.get("Ext. ID") or "").strip()

        if ext_id and ext_id.lower() != "none":
            merged_rows.append(row.to_dict())
        elif merged_rows:
            last = merged_rows[-1]
            for col in df.columns:

                if col in NON_MERGEABLE_COLUMNS:
                    continue

                val = row.get(col)
                if val and str(val).strip():
                    last[col] = (
                        f"{last[col]} {val}".strip()
                        if last.get(col)
                        else val
                    )

    if not merged_rows:
        return df

    return pd.DataFrame(merged_rows, columns=df.columns)


def validate_config_table(df: pd.DataFrame) -> pd.DataFrame:

    missing_columns = [
        canonical
        for canonical in REQUIRED_COLUMN_PREFIXES.values()
        if canonical not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {missing_columns}. "
            f"Columns found in PDF: {list(df.columns)}"
        )

    return df


def load_configuration(file_path: str) -> dict:
    """
    Returns the configuration grouped by sub-category (the
    "Category" column), preserving the original flat shape used by
    the output Excel. Each parameter also carries its own
    "main_category" so callers that need the section-level grouping
    (classification, retrieval) can build it via
    build_main_category_map() below.
    """

    df = extract_table_from_pdf(file_path)
    df = validate_config_table(df)

    # Replace empty strings / NaN with None
    df = df.where(pd.notnull(df) & (df != ""), None)

    configuration = {}

    for _, row in df.iterrows():

        category = str(row["Category"]).strip()

        parameter = {
            "ext_id": str(row["Ext. ID"]).strip(),
            "main_category": (
                row.get("Main Category") or UNCATEGORIZED_MAIN_CATEGORY
            ),
            "parameter": row["Parameter / Field to Extract"],
            "guidance": row["Extraction Guidance (AI instruction)"],
            "keywords": row["Typical Clause Keywords"],
            "data_type": row["Data Type"],
            "allowed_values": row["Unit / Allowed Values"],
            "priority": row["Priority"],
            "target_field": row["Target MIL DS Field"],
            "ground_truth_value": row.get("Ground Truth Value"),
        }

        configuration.setdefault(category, []).append(parameter)

    return configuration


def build_main_category_map(configuration: dict) -> dict:
    """
    Regroups the flat { sub_category: [parameters] } configuration
    into the true document hierarchy:

        { main_category: { sub_category: [parameters] } }

    e.g. "1. DOCUMENT & PROJECT IDENTIFICATION" -> "Doc ID" -> [...]

    This is the grouping used for page classification and
    category-level retrieval, since main categories are the coarse,
    clearly-separable themes a page actually belongs to - the
    sub-category (and Ext. ID) is a finer breakdown *within* that
    theme, not something a whole page is usually "about".
    """

    main_category_map = {}

    for sub_category, parameters in configuration.items():

        for parameter in parameters:

            main_category = (
                parameter.get("main_category")
                or UNCATEGORIZED_MAIN_CATEGORY
            )

            main_category_map.setdefault(
                main_category, {}
            ).setdefault(
                sub_category, []
            ).append(parameter)

    return main_category_map


def load_configuration_dataframe(file_path: str) -> pd.DataFrame:

    df = extract_table_from_pdf(file_path)
    df = validate_config_table(df)

    return df


def get_categories(configuration: dict):
    """Returns the sub-category names (used by the upload endpoint response)."""
    return list(configuration.keys())


def get_main_categories(configuration: dict):
    """Returns the main-category (section) names."""
    return list(build_main_category_map(configuration).keys())


def get_category_configuration(configuration: dict, category: str):
    return configuration.get(category, [])
