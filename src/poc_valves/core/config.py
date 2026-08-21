"""
Configuration loader for poc-valves.

Reads ``config/settings.yml`` and ``config/prompts.yml`` once at import
time and exposes typed accessors.  Values can be overridden via
environment variables (env vars take precedence over YAML for LLM
settings).

Usage::

    from poc_valves.core.config import settings, get_prompt

    reference_path = settings.reference_guide_path
    system_msg = get_prompt("tags_extraction.system_prompt")
    user_msg = get_prompt("tags_extraction.user_prompt", guide="...", pages_json="...")
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Resolve config directory (project_root / config)
# ---------------------------------------------------------------------------
# File lives at src/poc_valves/core/config.py, so parents[3] is the repo root
# (parents[0]=core, [1]=poc_valves, [2]=src, [3]=repo root).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_DIR = _PROJECT_ROOT / "config"

_SETTINGS_PATH = _CONFIG_DIR / "settings.yml"
_PROMPTS_PATH = _CONFIG_DIR / "prompts.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and return a YAML file as a dictionary."""
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


_ENV_OVERRIDES: dict[str, str] = {
    "REFERENCE_GUIDE_PATH": "reference_guide_path",
    "OPENAI_MODEL": "llm.model",
    "OPENAI_TEMPERATURE": "llm.temperature",
    "OPENAI_PROMPT_COST_PER_1K": "llm.prompt_cost_per_1k",
    "OPENAI_COMPLETION_COST_PER_1K": "llm.completion_cost_per_1k",
    "OPENAI_USD_TO_INR_RATE": "llm.usd_to_inr_rate",
    "SV2_LLM_MAX_CONCURRENCY": "llm.max_concurrency",
    "POSTPROCESSING_USE_ENGINEERED_DEFAULTS": (
        "postprocessing.use_engineered_defaults"
    ),
}


def _deep_set(
    d: dict, dotted_key: str, value: Any
) -> None:
    """Set a nested value using a dotted key like 'llm.model'."""
    keys = dotted_key.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _apply_env_overrides(data: dict) -> None:
    """Apply environment variable overrides to the settings data."""
    for env_var, dotted_key in _ENV_OVERRIDES.items():
        raw = os.getenv(env_var)
        if raw is not None:
            # cast to the same type already in the dict (float vs str)
            existing = data
            for part in dotted_key.split("."):
                if isinstance(existing, dict):
                    existing = existing.get(part, None)
                else:
                    existing = None
            if isinstance(existing, float):
                try:
                    raw = float(raw)
                except ValueError:
                    pass
            elif isinstance(existing, bool):
                raw = raw.lower() in ("true", "1", "yes")
            elif isinstance(existing, int):
                try:
                    raw = int(raw)
                except ValueError:
                    pass
            _deep_set(data, dotted_key, raw)


# ---------------------------------------------------------------------------
# Load settings
# ---------------------------------------------------------------------------
_settings_raw: dict[str, Any] = _load_yaml(_SETTINGS_PATH)
_apply_env_overrides(_settings_raw)


class _Settings:
    """Thin wrapper that exposes settings as attributes."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    # Top-level keys
    @property
    def reference_guide_path(self) -> Path:
        """Return the absolute path to the reference guide (field
        registry) YAML file, resolved relative to the project root if the
        configured path isn't already absolute."""
        rp = Path(
            self._data.get("reference_guide_path", "config/reference.yml")
        )
        return rp if rp.is_absolute() else _PROJECT_ROOT / rp

    # LLM sub-dict
    @property
    def llm(self) -> dict[str, Any]:
        """Return the LLM configuration sub-dictionary."""
        return self._data.get("llm", {})

    @property
    def model(self) -> str:
        """Return the LLM model name."""
        return str(self.llm.get("model", "gpt-4o"))

    @property
    def temperature(self) -> float:
        """Return the LLM temperature setting."""
        return float(self.llm.get("temperature", 1.0))

    @property
    def prompt_cost_per_1k(self) -> float:
        """Return the prompt cost per 1k tokens."""
        return float(self.llm.get("prompt_cost_per_1k", 0.0))

    @property
    def completion_cost_per_1k(self) -> float:
        """Return the completion cost per 1k tokens."""
        return float(self.llm.get("completion_cost_per_1k", 0.0))

    @property
    def usd_to_inr_rate(self) -> float:
        """Return the static USD -> INR conversion rate used for the Rs
        figure shown alongside USD cost in logs."""
        return float(self.llm.get("usd_to_inr_rate", 0.0))

    @property
    def max_concurrency(self) -> int:
        """Return the max number of concurrent tag-wise LLM extraction
        calls the SV2 pipeline issues at once."""
        return int(self.llm.get("max_concurrency", 5))

    # Postprocessing sub-dict
    @property
    def postprocessing(self) -> dict[str, Any]:
        """Return the postprocessing configuration sub-dictionary."""
        return self._data.get("postprocessing", {})

    @property
    def use_engineered_defaults(self) -> bool:
        """Return whether to use engineered defaults."""
        return bool(
            self.postprocessing.get("use_engineered_defaults", True)
        )

    # Prompt keys sub-dict
    @property
    def prompt_keys(self) -> dict[str, str]:
        """Return the prompt keys configuration sub-dictionary."""
        return self._data.get("prompt_keys", {})

    def __repr__(self) -> str:
        return f"_Settings({self._data!r})"


settings = _Settings(_settings_raw)


# ---------------------------------------------------------------------------
# Load prompts (nested dict structure)
# ---------------------------------------------------------------------------
_prompts_raw: dict[str, Any] = _load_yaml(_PROMPTS_PATH)

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _resolve_nested(data: dict, dotted_name: str) -> str | None:
    """Traverse a nested dict using dot notation (e.g. 'tags_extraction.system_prompt')."""
    keys = dotted_name.split(".")
    current: Any = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current if isinstance(current, str) else None


def _collect_leaf_keys(data: dict, prefix: str = "") -> list[str]:
    """Collect all leaf string values in a nested dict as dot-notation keys."""
    leaves: list[str] = []
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            leaves.extend(_collect_leaf_keys(value, full_key))
        elif isinstance(value, str):
            leaves.append(full_key)
    return leaves


_available_prompts: list[str] = _collect_leaf_keys(_prompts_raw)


def get_prompt(name: str, **kwargs: str) -> str:
    """Return the prompt text for *name*, with ``{{placeholder}}``
    values substituted from *kwargs*.

    Supports dot notation for nested prompts, e.g.
    ``tags_extraction.system_prompt``.

    Raises ``KeyError`` if the prompt name does not exist in the YAML
    file. Raises ``ValueError`` if required placeholders are missing.
    """
    text = _resolve_nested(_prompts_raw, name)

    if text is None:
        available = ", ".join(_available_prompts)
        raise KeyError(
            f"Prompt '{name}' not found in prompts.yml. "
            f"Available: {available}"
        )

    text = text.strip("\n")

    required = set(_PLACEHOLDER_RE.findall(text))
    provided = set(kwargs.keys())
    missing = required - provided
    if missing:
        raise ValueError(
            f"Missing placeholder(s) for prompt '{name}': {missing}"
        )

    def _replace(m: re.Match) -> str:
        key = m.group(1)
        return kwargs.get(key, m.group(0))  # fallback to literal if not provided

    return _PLACEHOLDER_RE.sub(_replace, text)


def get_system_prompt(name: str, **kwargs: str) -> str:
    """Shortcut: return the system message for a given prompt key."""
    return get_prompt(name, **kwargs)


def list_prompts() -> list[str]:
    """Return all available prompt names (dot-notation for nested entries)."""
    return sorted(_available_prompts)


# ---------------------------------------------------------------------------
# Load reference fields (path configurable via settings.yml's
# reference_guide_path / the REFERENCE_GUIDE_PATH env var — see
# _Settings.reference_guide_path).
# ---------------------------------------------------------------------------
_reference_raw: dict[str, Any] = _load_yaml(settings.reference_guide_path)


def load_reference_fields() -> list[dict[str, Any]]:
    """Return the list of field definitions from reference.yml.

    Each entry is a dict with keys: id, sv2_field_name, extraction_type,
    requirement, unit, source (dict), logic, aliases, output_spec, etc.
    """
    fields = _reference_raw.get("document_extraction_fields", [])
    if not isinstance(fields, list):
        return []
    return fields
