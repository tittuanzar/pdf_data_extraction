def build_accuracy_prompt(comparisons: list):
    """
    comparisons: list of dicts, each:
        { ext_id, parameter, data_type, extracted_value, ground_truth_value }
    """

    comparison_text = ""

    for item in comparisons:

        comparison_text += f"""
EXT ID: {item['ext_id']}
PARAMETER: {item['parameter']}
DATA TYPE: {item['data_type']}
SYSTEM EXTRACTED VALUE: {item['extracted_value']}
GROUND TRUTH VALUE (worked example): {item['ground_truth_value']}
"""

    prompt = f"""
You are validating an automated document-extraction pipeline against a
known-correct worked example.

For each field below, compare the SYSTEM EXTRACTED VALUE to the GROUND
TRUTH VALUE and classify the comparison.

CLASSIFICATION RULES:

- "match": the values are the same or semantically equivalent. Ignore
  differences in formatting, units notation, capitalization, word order,
  abbreviation vs. full term, or a referenced standard written slightly
  differently - as long as the underlying factual content is identical.
- "partial_match": the system value captures the correct information but
  is incomplete, less precise, or in a different form than expected
  (e.g. a range where a single figure was expected, a general clause
  covering the value but not stating it explicitly).
- "mismatch": the system value contradicts the ground truth, is
  factually wrong, or is null/missing while the ground truth has a real
  value.
- "not_applicable": both the system value and the ground truth are
  empty/null - nothing to compare.

Do not penalize differences in phrasing or units if the underlying
meaning is the same. Focus on whether the correct information was
found, not on exact string matching.

FIELDS TO COMPARE:

{comparison_text}

Return ONLY valid JSON using this structure. The "ext_id" value in
your response MUST be copied EXACTLY, character-for-character, from
the "EXT ID:" field above for each item - do not reformat, retype,
reorder, or normalize it in any way.

{{
    "comparisons": [
        {{
            "ext_id": "GS-001",
            "match_status": "match",
            "notes": "brief one-line justification"
        }}
    ]
}}

Return one entry for every Ext ID listed above.
"""

    return prompt
