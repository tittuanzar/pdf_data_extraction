import json
from openai import OpenAI

from poc_valves.pdf_extraction.core.config import settings
from poc_valves.pdf_extraction.prompts.extraction_prompt import build_extraction_prompt


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def extract_category(
    category: str,
    parameters: list,
    pages: list,
    parameter_page_hints: dict = None
):

    prompt = build_extraction_prompt(
        category=category,
        parameters=parameters,
        pages=pages,
        parameter_page_hints=parameter_page_hints
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
                "content": "You extract structured information from technical documents."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    content = response.choices[0].message.content

    return json.loads(content)
