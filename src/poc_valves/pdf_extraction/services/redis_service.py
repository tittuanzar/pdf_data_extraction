import json
import redis

from poc_valves.pdf_extraction.core.config import settings


redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True
)


def store_page(
    request_id: str,
    page_number: int,
    content: str,
    categories: list,
    embedding: list
):

    key = f"pdf:{request_id}:page:{page_number}"

    data = {
        "page_number": page_number,
        "content": content,
        "categories": categories,
        "embedding": embedding
    }

    redis_client.set(
        key,
        json.dumps(data),
        ex=settings.REDIS_TTL
    )


def get_pages_for_category(
    request_id: str,
    category: str,
    category_embedding: list = None,
    top_k: int = None,
    similarity_floor: float = None
):
    """
    Returns the pages relevant to a category using a hybrid match:

    - pages the LLM classification step explicitly tagged with this
      category, and
    - the top-K pages by cosine similarity between the category's
      embedding and each page's embedding (vector search).

    Vector search is rank-based (top-K), not gated by a fixed
    absolute similarity score - an absolute cutoff is unreliable
    across documents/domains and can silently disable the similarity
    branch entirely if set too high. `similarity_floor` is only a
    sanity check to exclude pages that are clearly unrelated noise,
    not the primary filter. Results are returned most-similar first.
    """

    # Imported here rather than at module load time to avoid a
    # circular-import error if something in this project's import
    # graph ends up loading redis_service and embedding_service in
    # a cycle. By call time (first actual request) every module has
    # already finished loading, so this always resolves cleanly.
    from poc_valves.pdf_extraction.services.embedding_service import cosine_similarity

    if top_k is None:
        top_k = settings.SIMILARITY_TOP_K

    if similarity_floor is None:
        similarity_floor = settings.SIMILARITY_FLOOR

    pattern = f"pdf:{request_id}:page:*"

    keys = redis_client.keys(pattern)

    all_pages = []

    for key in keys:
        all_pages.append(
            json.loads(redis_client.get(key))
        )

    scored = []

    if category_embedding is not None:

        for page in all_pages:

            if not page.get("embedding"):
                continue

            score = cosine_similarity(
                category_embedding,
                page["embedding"]
            )

            if score >= similarity_floor:
                scored.append((page["page_number"], score))

        scored.sort(key=lambda item: item[1], reverse=True)

    top_k_page_numbers = {
        page_number for page_number, _ in scored[:top_k]
    }

    similarity_by_page = dict(scored)

    result_pages = []

    for page in all_pages:

        page_number = page["page_number"]

        matched_by_tag = category in page.get("categories", [])
        matched_by_similarity = page_number in top_k_page_numbers

        if matched_by_tag or matched_by_similarity:
            page["similarity_score"] = similarity_by_page.get(page_number)
            result_pages.append(page)

    result_pages.sort(
        key=lambda page: page.get("similarity_score") or 0,
        reverse=True
    )

    return result_pages


def delete_request_data(request_id: str):

    pattern = f"pdf:{request_id}:page:*"

    keys = redis_client.keys(pattern)

    if keys:
        redis_client.delete(*keys)
