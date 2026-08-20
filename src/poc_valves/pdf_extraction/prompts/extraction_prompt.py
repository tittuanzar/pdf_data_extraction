def build_extraction_prompt(
    category: str,
    parameters: list,
    pages: list,
    parameter_page_hints: dict = None
):
    """
    parameter_page_hints: optional { ext_id: [page_number, ...] }
    produced by vector similarity search (see
    embedding_service.rank_pages_by_similarity), most-relevant page
    first. Surfaced as a hint per field so the model knows where to
    look first - it is a hint, not a restriction, since the value
    may still legitimately appear elsewhere in the provided pages.
    """

    parameter_page_hints = parameter_page_hints or {}

    parameter_text = ""

    for parameter in parameters:

        hint_pages = parameter_page_hints.get(parameter["ext_id"])

        hint_line = ""
        if hint_pages:
            pages_str = ", ".join(str(p) for p in hint_pages)
            hint_line = (
                f"LIKELY PAGES (by semantic similarity - check these "
                f"first, but the value may also be elsewhere in the "
                f"provided pages): {pages_str}\n"
            )

        parameter_text += f"""
EXT ID: {parameter['ext_id']}
PARAMETER: {parameter['parameter']}
GUIDANCE: {parameter['guidance']}
TYPICAL CLAUSE KEYWORDS: {parameter['keywords']}
DATA TYPE: {parameter['data_type']}
UNIT / ALLOWED VALUES: {parameter['allowed_values']}
PRIORITY: {parameter['priority']}
TARGET MIL DS FIELD: {parameter['target_field']}
{hint_line}
"""

    page_text = ""

    for page in pages:

        page_text += f"""
--- PAGE {page['page_number']} ---
{page['content']}
"""

    prompt = f"""
You are a structured document information extraction system.

CATEGORY:
{category}

FIELDS TO EXTRACT:

{parameter_text}

DOCUMENT CONTENT:

{page_text}

INSTRUCTIONS:

1. Extract values only from the provided document content.
2. Follow the extraction guidance for each field.
3. DATA TYPE and UNIT / ALLOWED VALUES describe the expected FORM of
   the answer once it is found (e.g. a number, an enumerated option,
   yes/no) - they are formatting guidance, NOT a filter on whether
   to extract. If the document only provides the relevant
   information in a different form than the stated data type (e.g.
   a referenced standard/method instead of a raw number, a
   descriptive clause instead of a strict enum value, a range
   instead of a single figure), still extract that information as
   the value exactly as written, and note the form mismatch in
   remarks_validation. Never withhold a value just because its
   literal form doesn't match the stated data type.
4. Do not invent or assume values.
5. Return null ONLY if the document truly contains no information
   relevant to this field anywhere in the provided pages - not
   because the information found doesn't match the expected data
   type or format.
6. Provide the exact supporting source clause when available.
7. Provide the page number containing the evidence.
8. If conflicting values are found across pages, extract the value
   from the most authoritative/specific source (e.g. a datasheet
   line over a general note) and mention the conflict in remarks.
9. Return one result for every configured Ext. ID.
10. The document may use different terminology, abbreviations, or
    phrasing than the field's PARAMETER name (synonyms, industry
    jargon, section titles worded differently). Use the GUIDANCE and
    TYPICAL CLAUSE KEYWORDS to judge relevance - do not rely on an
    exact text match of the parameter name.
11. LIKELY PAGES hints point to where a field is probably located,
    based on semantic similarity - always still scan the rest of the
    provided pages before concluding a value cannot be found.
12. Return ONLY valid JSON.

OUTPUT FORMAT:

{{
    "results": [
        {{
            "ext_id": "GS-001",
            "extracted_value": null,
            "source_clause": null,
            "source_page": null,
            "source_document": null,
            "remarks_validation": null
        }}
    ]
}}
"""

    return prompt
