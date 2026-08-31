from typing import Optional

import numpy as np
from openai import OpenAI

from poc_valves.pdf_extraction.core.config import settings
from poc_valves.pdf_extraction.services.usage_service import UsageTracker


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def create_embedding(text: str, tracker: Optional[UsageTracker] = None):
    """Embeds a single piece of text."""

    response = client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text
    )

    if tracker is not None:
        tracker.add_embedding_usage(response.usage, label="Embedding")

    return response.data[0].embedding


def create_embeddings(texts: list, tracker: Optional[UsageTracker] = None):
    """
    Embeds a batch of texts in a single API call. Order of the
    returned list matches the order of `texts`. Used to embed all
    main categories, or all parameters, at once instead of issuing
    one request per item.
    """

    if not texts:
        return []

    response = client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=texts
    )

    if tracker is not None:
        tracker.add_embedding_usage(response.usage, label="Embedding batch")

    return [item.embedding for item in response.data]


def build_main_category_embedding_text(
    main_category: str,
    sub_categories: dict
) -> str:
    """
    Builds a text representation of a *main* category (e.g.
    "1. DOCUMENT & PROJECT IDENTIFICATION") from its own name plus
    every sub-category and parameter nested underneath it, so the
    whole theme can be embedded once and compared against page
    embeddings via similarity search.
    """

    parts = [f"Main Category: {main_category}"]

    for sub_category, parameters in sub_categories.items():

        parts.append(f"Sub-category: {sub_category}")

        for parameter in parameters:
            parts.append(
                f"Parameter: {parameter.get('parameter')}. "
                f"Keywords: {parameter.get('keywords')}. "
                f"Guidance: {parameter.get('guidance')}"
            )

    return "\n".join(parts)


def build_parameter_embedding_text(parameter: dict) -> str:
    """
    Builds a text representation of a single field/parameter (one
    Ext. ID) from its name, extraction guidance, typical clause
    keywords, and allowed values/unit. This is deliberately narrower
    than a category embedding so it can be used to rank which pages
    are most likely to contain *this specific* value, catching
    synonym/terminology differences the raw parameter name alone
    wouldn't match.
    """

    return (
        f"Field: {parameter.get('parameter')}. "
        f"Guidance: {parameter.get('guidance')}. "
        f"Keywords / synonyms: {parameter.get('keywords')}. "
        f"Unit or allowed values: {parameter.get('allowed_values')}."
    )


def cosine_similarity(vec_a, vec_b) -> float:

    a = np.array(vec_a, dtype=float)
    b = np.array(vec_b, dtype=float)

    denom = np.linalg.norm(a) * np.linalg.norm(b)

    if denom == 0:
        return 0.0

    return float(np.dot(a, b) / denom)


def rank_pages_by_similarity(
    target_embedding: list,
    pages: list,
    top_n: int = 3
):
    """
    Ranks a set of pages (each a dict with "page_number" and
    "embedding") against a target embedding (e.g. a single
    parameter's embedding) and returns the top-N page numbers, most
    similar first. Used to build "likely pages" hints per field
    inside the extraction prompt.
    """

    scored = [
        (
            page["page_number"],
            cosine_similarity(target_embedding, page["embedding"])
        )
        for page in pages
        if page.get("embedding")
    ]

    scored.sort(key=lambda item: item[1], reverse=True)

    return [page_number for page_number, _ in scored[:top_n]]
