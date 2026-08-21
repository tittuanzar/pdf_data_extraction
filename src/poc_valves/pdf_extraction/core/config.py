from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# File lives at src/poc_valves/pdf_extraction/core/config.py, so parents[4]
# is the repo root (parents[0]=core, [1]=pdf_extraction, [2]=poc_valves,
# [3]=src, [4]=repo root).
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    OPENAI_API_KEY: str

    OPENAI_CHAT_MODEL: str = "gpt-4.1-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    REDIS_TTL: int = 3600

    CONFIG_FILE: str = "storage/extraction_config.pdf"

    # Vector search retrieval for category -> page matching.
    #
    # A fixed absolute cosine-similarity cutoff is unreliable across
    # domains/embedding models (text-embedding-3-small scores for
    # genuinely related text often land well below 0.75). So
    # retrieval is top-K based: take the K most similar pages to a
    # category, then use SIMILARITY_FLOOR only to drop pages that
    # are clearly irrelevant noise, not to gate whether search
    # contributes at all.
    SIMILARITY_TOP_K: int = 10
    SIMILARITY_FLOOR: float = 0.15

    # Per-parameter "likely pages" hints surfaced inside the
    # extraction prompt (see embedding_service.rank_pages_by_similarity).
    PARAMETER_HINT_TOP_N: int = 3

    # Number of Ext. ID comparisons sent per accuracy-evaluation LLM call.
    ACCURACY_BATCH_SIZE: int = 25

    # Max number of LLM requests issued at once when a phase (per-category
    # extraction, accuracy-batch scoring) has several independent calls to
    # make. Only affects PdfExtractionPipeline (the configurable/generic
    # document endpoint) - the tag-wise SV2 pipeline is unaffected.
    LLM_MAX_CONCURRENCY: int = 5

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        extra="ignore"
    )


settings = Settings()
