def build_category_prompt(
    main_category_map: dict,
    pages: list
):
    """
    main_category_map shape:
        { main_category: { sub_category: [parameters] } }

    Pages are classified against MAIN CATEGORIES only (e.g.
    "1. DOCUMENT & PROJECT IDENTIFICATION"). Sub-categories and
    parameters are included purely as context so the model
    understands what each main category actually covers - the
    returned category tag must always be a main category name.
    """

    category_text = ""

    for main_category, sub_categories in main_category_map.items():

        category_text += f"\nMAIN CATEGORY: {main_category}\n"

        for sub_category, parameters in sub_categories.items():

            category_text += f"  Sub-topic: {sub_category}\n"

            for parameter in parameters:
                category_text += (
                    f"    - Parameter: {parameter['parameter']}\n"
                    f"      Keywords: {parameter['keywords']}\n"
                )

    page_text = ""

    for page in pages:

        page_text += (
            f"\n--- PAGE {page['page_number']} ---\n"
            f"{page['content']}\n"
        )

    prompt = f"""
You are a document classification system.

Your task is to identify which MAIN CATEGORY (not sub-topic) each
document page belongs to.

A page may belong to multiple main categories.

Assign a main category when the page contains information relevant
to ANY of the sub-topics or parameters listed underneath it - the
sub-topics and parameters are shown only so you understand the
scope of each main category. Do not return a sub-topic name; always
return the exact MAIN CATEGORY text.

Do not assign a category only because an isolated keyword appears
with no real relevance.

If no main category is relevant, return an empty list.

AVAILABLE MAIN CATEGORIES (WITH SUB-TOPICS FOR CONTEXT):

{category_text}

DOCUMENT PAGES:

{page_text}

Return ONLY valid JSON using this structure:

{{
    "pages": [
        {{
            "page_number": 1,
            "categories": [
                {{
                    "category": "exact main category text",
                    "confidence": 0.95
                }}
            ]
        }}
    ]
}}
"""

    return prompt
