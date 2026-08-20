# classify each page per category. Sends all pages + the list of Main Categories

import json
from openai import OpenAI

from poc_valves.pdf_extraction.core.config import settings
from poc_valves.pdf_extraction.prompts.category_prompt import build_category_prompt


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def classify_pages(main_category_map: dict, pages: list):
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

    content = response.choices[0].message.content

    return json.loads(content)
