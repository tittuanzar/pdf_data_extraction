"""Thread-safe token-usage/cost accounting for the generic PDF extraction
pipeline (:class:`~poc_valves.pdf_extraction.services.pipeline_service.PdfExtractionPipeline`).

Mirrors the SV2 pipeline's aggregate usage/cost logging (see
``core.llm.client.LLMClient`` and ``core.pipeline.sv2_pipeline.SV2Pipeline``),
adapted for this pipeline's several independent OpenAI call sites
(classification, per-category extraction, accuracy scoring, embeddings)
instead of one shared client wrapper.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from poc_valves.pdf_extraction.core.config import settings

logger = logging.getLogger(__name__)


def format_cost(cost_usd: float) -> str:
    """Format a USD cost for logging, with its Rs equivalent alongside it
    using the configured static ``OPENAI_USD_TO_INR_RATE``."""
    if not cost_usd:
        return "(not configured)"
    if settings.OPENAI_USD_TO_INR_RATE:
        return f"${cost_usd:.6f} (₹{cost_usd * settings.OPENAI_USD_TO_INR_RATE:.4f})"
    return f"${cost_usd:.6f}"


def _get(usage: Any, key: str) -> int:
    raw = usage.get(key, 0) if isinstance(usage, dict) else getattr(usage, key, 0)
    return int(raw or 0)


class UsageTracker:
    """Accumulates chat + embedding token usage/cost across every LLM call
    made during one ``process_pdf`` request.

    Classification, per-category extraction, and accuracy-batch scoring
    each run several independent calls concurrently via ThreadPoolExecutor
    (see ``pipeline_service.process_pdf``), so updates are lock-protected -
    the same concern the SV2 pipeline's ``_accumulate_usage`` handles by
    only merging each worker's own local totals after ``future.result()``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.embedding_tokens = 0
        self.cost_usd = 0.0

    def add_chat_usage(
        self, usage: Optional[Any], *, label: str = "LLM call"
    ) -> None:
        """Record one chat-completion call's token usage and cost."""
        if usage is None:
            return
        prompt_tokens = _get(usage, "prompt_tokens")
        completion_tokens = _get(usage, "completion_tokens")
        cost = (
            prompt_tokens / 1000.0 * settings.OPENAI_PROMPT_COST_PER_1K
            + completion_tokens
            / 1000.0
            * settings.OPENAI_COMPLETION_COST_PER_1K
        )
        with self._lock:
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.cost_usd += cost
        logger.info(
            "%s usage: prompt=%s completion=%s cost=%s",
            label,
            prompt_tokens,
            completion_tokens,
            format_cost(cost),
        )

    def add_embedding_usage(
        self, usage: Optional[Any], *, label: str = "Embedding call"
    ) -> None:
        """Record one embeddings call's token usage and cost."""
        if usage is None:
            return
        tokens = _get(usage, "total_tokens")
        cost = tokens / 1000.0 * settings.OPENAI_EMBEDDING_COST_PER_1K
        with self._lock:
            self.embedding_tokens += tokens
            self.cost_usd += cost
        logger.info("%s usage: tokens=%s cost=%s", label, tokens, format_cost(cost))

    def log_summary(self, *, label: str = "PDF extraction pipeline") -> None:
        """Log one aggregate token-usage/cost line for the whole request,
        matching SV2Pipeline.run's final summary line."""
        total_tokens = (
            self.prompt_tokens + self.completion_tokens + self.embedding_tokens
        )
        logger.info(
            "%s TOTAL usage for this request — "
            "prompt=%s completion=%s embedding=%s total=%s cost=%s",
            label,
            self.prompt_tokens,
            self.completion_tokens,
            self.embedding_tokens,
            total_tokens,
            format_cost(self.cost_usd),
        )
