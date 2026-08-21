"""Three-phase SV2 valve datasheet extraction pipeline.

  1. Direct-field + auxiliary verbatim extraction (LLM, narrow scope, no
     computation) — ``SV2FieldExtractor.extract_page``.
  2. Derived-field computation: deterministic Python rules first
     (``postprocess.apply_pre_llm_derivations``/``apply_post_llm_derivations``),
     then one focused LLM call per tag for the remaining judgment-based
     fields (``SV2FieldExtractor.derive_judgment_fields``), using Phase 1's
     output only.
  3. Engineered-field defaults (deterministic, org-standard, no LLM;
     ``postprocess.apply_engineered_defaults``).

Each tag spans a main datasheet page plus one or more continuation pages
(process notes, MDMT, IBR applicability, etc.) — pages are first grouped by
tag (``_group_pages_by_tag``) and merged (``_merge_page_group``) so Phase 1
sees a tag's full text in a single call.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ..config import settings
from ..llm.extractor import (
    SV2FieldExtractor,
    _normalize_decimal_commas,  # noqa: F401 -- re-exported for tests
    _normalize_tag_no,  # noqa: F401 -- re-exported for tests
)
from ..models import ParsedOutput, PartialSV2ValveDatasheet, SV2ValveDatasheet
from . import postprocess

logger = logging.getLogger(__name__)

# A tag's enquiry data spans a "main" datasheet page — starts with the
# row "1 Tag No. ..." — followed by one or more continuation pages that
# start with "Tag Number: ..." and carry Specification/Process Notes
# (MDMT, IBR applicability, etc. — inputs several derived-field rules
# depend on). Pages matching neither pattern (e.g. a cover/title page)
# carry no tag data at all.
_MAIN_PAGE_RE = re.compile(r"1\s+Tag\s*No\.", re.IGNORECASE)
_CONTINUATION_PAGE_RE = re.compile(r"Tag\s*Number\s*:", re.IGNORECASE)


def _group_pages_by_tag(pages: list[dict]) -> list[list[dict]]:
    """Group PDF pages into per-tag page groups.

    Without this, treating every PDF page as its own tag (the previous
    behaviour) produces a bogus extra sheet per continuation page — mostly
    empty, duplicate tag number — instead of merging it into the main
    page's tag, and it means the process-notes text a continuation page
    carries never reaches the same Phase-1 call as its tag's main page.
    """
    groups: list[list[dict]] = []
    for p in pages:
        text = str(p.get("text", "") or "")
        if _MAIN_PAGE_RE.search(text):
            groups.append([p])
        elif _CONTINUATION_PAGE_RE.search(text):
            if groups:
                groups[-1].append(p)
            else:
                # Continuation page with no preceding main page in this
                # batch — keep it as its own group rather than dropping it.
                groups.append([p])
        # else: no tag markers on this page (e.g. cover page) — skip.
    return groups


def _merge_page_group(group: list[dict]) -> dict:
    """Merge one tag's page group into a single page-shaped dict for
    Phase 1 extraction: concatenated text/tables across all pages in the
    group, image taken from the main (first) page only.
    """
    merged_text = "\n\n".join(str(p.get("text", "") or "") for p in group)
    merged_tables = [t for p in group for t in (p.get("tables") or [])]
    return {
        "page_number": group[0].get("page_number"),
        "text": merged_text,
        "tables": merged_tables,
        "image_b64": group[0].get("image_b64", ""),
    }


def _accumulate_usage(total: dict[str, int], usage: Optional[dict]) -> None:
    """Add one LLM call's token usage dict into a running total dict."""
    if not usage:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key, 0) or 0)


def _finalize_tag(partial: PartialSV2ValveDatasheet) -> SV2ValveDatasheet:
    """Validate a fully-processed partial tag into the strict
    SV2ValveDatasheet.

    Any required field still missing after all three phases indicates a
    real extraction/derivation gap for that tag — log a warning and
    coerce it to a visible placeholder rather than failing the whole
    batch: this is a review workbook, so engineers need to see which
    cells need manual entry, not a hard crash.
    """
    data = partial.model_dump()
    missing = [
        name
        for name, field in SV2ValveDatasheet.model_fields.items()
        if field.is_required() and data.get(name) is None
    ]
    if missing:
        logger.warning(
            "Tag %s missing required fields after all phases: %s",
            data.get("tag_no"),
            missing,
        )
        for name in missing:
            data[name] = "NOT EXTRACTED"
    return SV2ValveDatasheet(**data)


class SV2Pipeline:
    """Orchestrates the three-phase SV2 extraction over a PDF's pages."""

    def __init__(
        self,
        extractor: Optional[SV2FieldExtractor] = None,
        *,
        use_engineered_defaults: Optional[bool] = None,
    ) -> None:
        self.extractor = extractor or SV2FieldExtractor()
        self._use_engineered_defaults = (
            settings.use_engineered_defaults
            if use_engineered_defaults is None
            else use_engineered_defaults
        )

    def _process_group(
        self, index: int, group: list[dict], guide: Optional[str]
    ) -> tuple[int, SV2ValveDatasheet, dict[str, int], float]:
        """Run Phases 1-3 for a single tag's page group.

        Returns ``(index, finalized_tag, usage, cost)``. ``index`` is the
        group's position in the original ``page_groups`` list, used by the
        caller to restore output order after concurrent execution; usage/
        cost are this tag's own totals, not accumulated into any shared
        state here, so the caller can merge them without a lock.
        """
        p = _merge_page_group(group)
        page_num = int(p.get("page_number", 0) or 0)
        logger.info(
            "Three-phase pipeline: processing tag starting at page %s "
            "(%d page(s) in group)",
            page_num,
            len(group),
        )

        usage_total: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        cost_total = 0.0

        # --- Phase 1: direct + auxiliary verbatim extraction ---
        direct_values, aux_values, usage, cost = self.extractor.extract_page(
            p.get("text", ""),
            page_num,
            image_b64=p.get("image_b64", ""),
            tables=p.get("tables"),
            guide=guide,
        )
        _accumulate_usage(usage_total, usage)
        cost_total += cost

        partial = PartialSV2ValveDatasheet(**direct_values)

        # --- Phase 2: derived fields ---
        partial = postprocess.apply_pre_llm_derivations(partial, aux_values)
        try:
            judgment, usage, cost = self.extractor.derive_judgment_fields(
                partial, aux_values
            )
            _accumulate_usage(usage_total, usage)
            cost_total += cost
            judgment_updates = {
                k: v
                for k, v in judgment.model_dump().items()
                if v is not None and getattr(partial, k, None) is None
            }
            if judgment_updates:
                partial = partial.model_copy(update=judgment_updates)
        except Exception:
            logger.exception(
                "Phase 2 LLM judgment call failed for tag %s; "
                "continuing with deterministic derivations only",
                direct_values.get("tag_no"),
            )
        partial = postprocess.apply_post_llm_derivations(partial, aux_values)

        # --- Phase 3: engineered defaults ---
        partial = postprocess.apply_engineered_defaults(
            partial,
            use_engineered_defaults=self._use_engineered_defaults,
        )

        return index, _finalize_tag(partial), usage_total, cost_total

    def run(
        self, pages: list[dict], guide: Optional[str] = None
    ) -> ParsedOutput:
        """Extract SV2 tags from a list of page dicts via the three phases,
        logging one aggregate token-usage/cost line for the whole request.
        """
        page_groups = _group_pages_by_tag(pages)
        logger.info(
            "Three-phase pipeline: %d page(s) grouped into %d tag(s)",
            len(pages),
            len(page_groups),
        )

        total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        total_cost = 0.0

        tags_by_index: dict[int, SV2ValveDatasheet] = {}

        # Force the OpenAI client to be constructed on this (main) thread
        # before any worker thread touches it. LLMClient.client lazily
        # builds and caches the OpenAI() client with a check-then-act
        # pattern that isn't safe if two worker threads race on first use.
        self.extractor.llm.client

        # Each tag's page group is processed independently (own LLM calls,
        # own local state), so they're run concurrently instead of one
        # after another - this is the dominant cost in the pipeline.
        with ThreadPoolExecutor(
            max_workers=settings.max_concurrency
        ) as executor:
            futures = [
                executor.submit(self._process_group, i, group, guide)
                for i, group in enumerate(page_groups)
            ]
            for future in as_completed(futures):
                index, tag, usage, cost = future.result()
                tags_by_index[index] = tag
                _accumulate_usage(total_usage, usage)
                total_cost += cost

        tags: list[SV2ValveDatasheet] = [
            tags_by_index[i] for i in range(len(page_groups))
        ]

        logger.info(
            "Three-phase pipeline TOTAL usage for this request: %d tag(s) "
            "from %d page(s) — prompt=%s completion=%s total=%s cost=%s",
            len(tags),
            len(pages),
            total_usage["prompt_tokens"],
            total_usage["completion_tokens"],
            total_usage["total_tokens"],
            self.extractor.llm.format_cost(total_cost),
        )

        return ParsedOutput(tags=tags)
