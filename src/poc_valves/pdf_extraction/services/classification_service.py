# classify each page per category. Sends all pages + the list of Main Categories

import json
from typing import Optional

from openai import OpenAI

from poc_valves.pdf_extraction.core.config import settings
from poc_valves.pdf_extraction.prompts.category_prompt import build_category_prompt
from poc_valves.pdf_extraction.services.usage_service import UsageTracker


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def classify_pages(
    main_category_map: dict, pages: list, tracker: Optional[UsageTracker] = None
):
    """
    Classifies each page against the MAIN CATEGORIES (the section
    headers, e.g. "1. DOCUMENT & PROJECT IDENTIFICATION") rather
    than the finer sub-categories/parameters underneath them.
    """

    prompt = build_category_prompt(
        main_category_map,
        pages
    )

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
                    "You classify document pages into predefined "
                    "main categories."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    if tracker is not None:
        tracker.add_chat_usage(response.usage, label="Page classification")

    content = response.choices[0].message.content

    return json.loads(content)
