"""Skill filesystem manager — materialize / read / delete Skill files on disk.

All Skill files (SKILL.md + auxiliary scripts / templates / etc.) are
stored under a configurable root directory (``SKILLS_DIR``).  MongoDB
only keeps the registration metadata; the actual file content lives on
the local filesystem so that Agent-executed bash commands can find the
files by absolute path.

Layout::

    SKILLS_DIR/
      my-skill/
        SKILL.md
        scripts/
          search.py
        templates/
          ...
"""
from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from app.core.config import settings
from app.engine.tool.skill_parser import SkillFileEntry


def _skills_dir() -> Path:
    """Return the configured Skill root directory.

    Reads ``SKILLS_CONTAINER_DIR`` from application settings (``.env`` /
    environment variable); defaults to ``~/.agent-flow/skills/``.
    """
    return Path(settings.SKILLS_CONTAINER_DIR).expanduser()




def get_skill_base_path(name: str) -> Path:
    """Return the absolute path for a Skill directory.

    Does **not** validate that the path exists.
    """
    return _skills_dir() / name


def materialize_skill(name: str, files: list[SkillFileEntry]) -> Path:
    """Write a list of Skill files to disk under ``SKILLS_DIR/{name}/``.

    Any existing directory is replaced atomically (write to a temp
    sibling, then ``os.replace``).

    Args:
        name: Skill name (used as the sub-directory name).
        files: List of file entries with ``path``, ``content``, ``size``.

    Returns:
        The absolute ``Path`` of the materialized Skill directory.
    """
    base = get_skill_base_path(name)
    staging = base.with_suffix(".tmp")

    # Clean up any leftover staging dir from a previous failed run
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    staging.mkdir(parents=True, exist_ok=True)

    for entry in files:
        target = staging / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry.content, encoding="utf-8")

    # Atomic swap: staging -> base
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    base.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(base)

    logger.info(
        "skill_materialized",
        skill_name=name,
        file_count=len(files),
        path=str(base),
    )
    return base


def read_skill_file(name: str, rel_path: str) -> str | None:
    """Read a single file from the Skill directory.

    Args:
        name: Skill name.
        rel_path: Relative path inside the Skill directory (e.g. ``SKILL.md``).

    Returns:
        File content as a UTF-8 string, or ``None`` if the file does
        not exist.
    """
    full = get_skill_base_path(name) / rel_path
    try:
        return full.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("skill_file_not_found", skill_name=name, rel_path=rel_path)
        return None
    except Exception as exc:
        logger.warning(
            "skill_file_read_error",
            skill_name=name,
            rel_path=rel_path,
            error=str(exc),
        )
        return None


def delete_skill_dir(name: str) -> bool:
    """Remove a Skill directory from disk.

    Args:
        name: Skill name.

    Returns:
        ``True`` if the directory existed and was removed, ``False``
        if it did not exist.
    """
    base = get_skill_base_path(name)
    if not base.exists():
        return False

    shutil.rmtree(base)
    logger.info("skill_dir_deleted", skill_name=name, path=str(base))
    return True


def list_skill_files(name: str) -> list[dict]:
    """Scan the Skill directory and return file entries for the tree view.

    Returns:
        List of dicts with ``path`` (relative) and ``size`` keys,
        suitable for ``_build_file_tree``.
    """
    base = get_skill_base_path(name)
    if not base.is_dir():
        return []

    entries: list[dict] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        entries.append({
            "path": str(rel),
            "size": p.stat().st_size,
        })
    return entries
