"""Backward-compatibility helpers for legacy field migrations.

Centralises legacy-field resolution (``tool_ids → skill_ids``,
``llm_config.* → flat fields``) so that each consumer only needs a
single function call instead of duplicated fallback logic.
"""
from __future__ import annotations


def resolve_skill_ids(doc: dict) -> list[str]:
    """Return the effective skill IDs from a MongoDB agent document.

    Prefers ``skill_ids`` when present and non-empty; falls back to
    the legacy ``tool_ids`` field for documents created before the
    migration.

    Args:
        doc: Raw MongoDB agent document.

    Returns:
        List of tool/skill ID strings (never ``None``).
    """
    skill_ids = doc.get("skill_ids") or []
    if not skill_ids:
        skill_ids = doc.get("tool_ids") or []
    return list(skill_ids)


def resolve_default_model(doc: dict) -> str:
    """Return the effective ``default_model`` from an agent document.

    Prefers the flat ``default_model`` field; falls back to the legacy
    nested ``llm_config.default_model`` for documents created before the
    field was flattened. Returns ``""`` when neither is set.
    """
    if doc.get("default_model"):
        return doc["default_model"]
    legacy = doc.get("llm_config") or {}
    return legacy.get("default_model", "")


def resolve_max_retry(doc: dict) -> int:
    """Return the effective ``max_retry`` from an agent document.

    Prefers the flat ``max_retry`` field; falls back to the legacy
    nested ``llm_config.max_retry`` (default 3).
    """
    if "max_retry" in doc:
        return int(doc["max_retry"])
    legacy = doc.get("llm_config") or {}
    return int(legacy.get("max_retry", 3))
