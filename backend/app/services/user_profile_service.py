"""UserProfileService — 用户记忆条目（per-user，跨 agent，§6）。

机制（§6.3）：
- 文本条目模式：add / replace(old_text→content) / remove + operations 批量原子
- 三层限制：总量 ≤1375 字符、单条 ≤200 字符、完全重复拒绝（幂等）
- 超限拒绝并返回现有条目 → 迫使 agent 合并/删旧后重试（自修剪）
"""
from __future__ import annotations

from loguru import logger

from app.db.mongodb import get_database
from app.models.base import generate_id, utc_now
from app.models.user_skill import (
    MEMORY_ENTRY_CHAR_LIMIT,
    MEMORY_TOTAL_CHAR_LIMIT,
)


class MemoryError(Exception):
    """记忆操作失败（工具层转 JSON 错误结果）。"""

    def __init__(self, message: str, *, entries: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.entries = entries


class UserProfileService:
    """用户记忆 CRUD（全部静态方法）。"""

    COLLECTION = "user_profiles"

    @staticmethod
    def _col():
        return get_database()[UserProfileService.COLLECTION]

    @staticmethod
    async def get_entries(user_id: str) -> list[str]:
        doc = await UserProfileService._col().find_one({"user_id": user_id})
        return list(doc.get("entries") or []) if doc else []

    @staticmethod
    def _total_chars(entries: list[str]) -> int:
        return sum(len(e) for e in entries)

    @staticmethod
    def _check_entry(content: str) -> str:
        content = (content or "").strip()
        if not content:
            raise MemoryError("Empty memory entry.")
        if len(content) > MEMORY_ENTRY_CHAR_LIMIT:
            raise MemoryError(
                f"Entry too long ({len(content)}/{MEMORY_ENTRY_CHAR_LIMIT} chars). "
                f"Compress it to a high-signal one-liner."
            )
        return content

    @staticmethod
    def _locate(entries: list[str], old_text: str) -> int:
        matches = [i for i, e in enumerate(entries) if e == old_text]
        if not matches:
            raise MemoryError(
                f"old_text not found (it may have changed or already been edited). "
                f"Current entries: {entries}"
            )
        if len(matches) > 1:
            raise MemoryError("old_text matches multiple entries; memory entries must be unique.")
        return matches[0]

    @staticmethod
    async def _save(user_id: str, entries: list[str]) -> None:
        total = UserProfileService._total_chars(entries)
        if total > MEMORY_TOTAL_CHAR_LIMIT:
            raise MemoryError(
                f"Memory at {total}/{MEMORY_TOTAL_CHAR_LIMIT} chars. Consolidate: use "
                f"'replace' to merge overlapping entries or 'remove' stale ones, then retry.",
                entries=entries,
            )
        await UserProfileService._col().update_one(
            {"user_id": user_id},
            {"$set": {"entries": entries, "updated_at": utc_now().isoformat()},
             "$setOnInsert": {"_id": generate_id("prof"), "user_id": user_id}},
            upsert=True,
        )

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    @staticmethod
    async def apply(
        user_id: str,
        *,
        action: str = "",
        content: str = "",
        old_text: str = "",
        operations: list[dict] | None = None,
    ) -> dict:
        """单条或批量（原子：全部应用后统一检查总量，§6.4）。"""
        entries = await UserProfileService.get_entries(user_id)

        ops: list[dict]
        if operations:
            ops = operations
        elif action:
            ops = [{"action": action, "content": content, "old_text": old_text}]
        else:
            raise MemoryError("Provide either action or operations.")

        applied: list[str] = []
        for op in ops:
            op_action = (op.get("action") or "").strip()
            if op_action == "add":
                entry = UserProfileService._check_entry(op.get("content", ""))
                if entry in entries:
                    applied.append(f"skipped duplicate: {entry[:50]}")
                    continue
                entries.append(entry)
                applied.append(f"added: {entry[:50]}")
            elif op_action == "replace":
                entry = UserProfileService._check_entry(op.get("content", ""))
                idx = UserProfileService._locate(entries, op.get("old_text", ""))
                entries[idx] = entry
                applied.append(f"replaced #{idx}")
            elif op_action == "remove":
                idx = UserProfileService._locate(entries, op.get("old_text", ""))
                entries.pop(idx)
                applied.append(f"removed #{idx}")
            else:
                raise MemoryError(f"Unknown memory action '{op_action}' (add/replace/remove).")

        await UserProfileService._save(user_id, entries)
        logger.info("user_memory_updated", user_id=user_id, ops=applied)
        return {
            "ok": True,
            "applied": applied,
            "entries": entries,
            "usage": f"{UserProfileService._total_chars(entries)}/{MEMORY_TOTAL_CHAR_LIMIT} chars",
        }

    @staticmethod
    async def set_entries(user_id: str, entries: list[str]) -> dict:
        """整体替换（"我的记忆"页编辑器用）。逐条校验 + 总量校验。"""
        cleaned = [UserProfileService._check_entry(e) for e in entries]
        # 去重保持顺序
        seen: set[str] = set()
        deduped: list[str] = []
        for e in cleaned:
            if e not in seen:
                seen.add(e)
                deduped.append(e)
        await UserProfileService._save(user_id, deduped)
        return {"ok": True, "entries": deduped}

    @staticmethod
    async def clear(user_id: str) -> dict:
        await UserProfileService._col().delete_one({"user_id": user_id})
        return {"ok": True, "entries": []}


__all__ = ["UserProfileService", "MemoryError"]
