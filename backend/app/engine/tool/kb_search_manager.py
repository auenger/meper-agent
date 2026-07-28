"""Vector KB search tool — ``kb_search`` (closure-injected).

Mirrors the ``KbManager`` pattern from ``kb_manager.py``: a stateful manager
constructed once per ``resolve_harness_context`` call, closure-capturing the
vector KBs the agent is bound to, producing a single ``StructuredTool`` for
the LLM.

The tool count is FIXED (one ``kb_search``), independent of how many vector
KBs are bound — the LLM picks a specific KB via the optional ``kb_id``
argument, or searches all bound vector KBs when it's omitted. Each bound KB's
name + description is listed in the tool description so the LLM can choose.

Tree KBs (``kb_glob`` / ``kb_grep`` / ``kb_read``) and vector KBs
(``kb_search``) coexist: an agent bound to both kinds gets all four tools,
with naturally distinct names.
"""
from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.config import settings


class _KbSearchArgs(BaseModel):
    query: str = Field(..., description="检索查询文本")
    kb_id: str | None = Field(
        None, description="限定单个知识库；省略则跨所有绑定的向量知识库检索"
    )
    top_k: int = Field(
        default=5, ge=1, le=20, description="返回结果数量（默认 5）"
    )


class KbSearchManager:
    """Build a vector-KB-bound ``kb_search`` tool for one agent resolve.

    Args:
        vector_kb_infos: mapping of ``kb_id -> "name — description"`` for
            every vector KB the agent is bound to. Embedded into the tool
            description so the LLM knows which KBs are searchable.
    """

    def __init__(self, vector_kb_infos: dict[str, str]):
        self._kb_infos = vector_kb_infos

    async def search(self, query: str, kb_id: str | None = None, top_k: int = 5) -> str:
        """Run hybrid retrieval across the bound vector KBs.

        Returns a JSON string of ``[{kb_id, text, score, source_file, page}]``.
        """
        from app.engine.tool.kb_retriever import retrieve

        target_ids = [kb_id] if kb_id and kb_id in self._kb_infos else list(self._kb_infos)
        all_results: list[dict] = []
        for kid in target_ids:
            try:
                res = await retrieve(kid, query, top_k=top_k)
            except Exception as exc:
                # One KB failing shouldn't abort the whole search.
                all_results.append({"kb_id": kid, "error": str(exc)})
                continue
            for r in res:
                all_results.append({"kb_id": kid, **r})
        return json.dumps(all_results, ensure_ascii=False)

    def make_tools(self) -> list[StructuredTool]:
        """Produce the single ``kb_search`` StructuredTool."""
        # List every bound KB's id + name/description so the LLM can choose.
        kb_hint = (
            "; ".join(f"{kid}={info}" for kid, info in self._kb_infos.items())
            or "none"
        )

        async def _search_coro(
            query: str, kb_id: str | None = None, top_k: int = 5
        ) -> str:
            effective_top_k = top_k or settings.KB_VECTOR_TOP_K
            return await self.search(query, kb_id, effective_top_k)

        tool = StructuredTool.from_function(
            _search_coro,
            name="kb_search",
            description=(
                "在向量知识库中语义检索相关文档片段，返回 文本+相关度评分+来源。"
                " 适用于大文档语义检索、FAQ、产品手册等。"
                f" 可用知识库: {kb_hint}"
            ),
            args_schema=_KbSearchArgs,
            coroutine=_search_coro,
        )
        return [tool]


__all__ = ["KbSearchManager"]
