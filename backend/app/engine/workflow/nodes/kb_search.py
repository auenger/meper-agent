"""Workflow ``kb_search`` node — vector KB retrieval for workflows.

Config schema::

    {
        "kb_ids": ["kb_v1", ...],      # vector KBs to search
        "query": "{{start.input}}",     # query text (supports {{var}} refs)
        "top_k": 5                      # optional, defaults to settings
    }

Output (written to the variable pool under the node id)::

    {"results": [{text, score, doc_id, source_file, page}, ...], "query": "..."}

Downstream nodes (e.g. an LLM node) reference the chunks via
``{{node_id.results}}`` to use them as retrieval-augmented context.

Only vector-type KBs are searchable here; tree-type KBs are agent-only
(explored via kb_glob/grep/read in the Agent runtime).
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.engine.workflow.node_executor import BaseNodeExecutor, NodeResult


class KbSearchNodeExecutor(BaseNodeExecutor):
    """Retrieve chunks from one or more vector KBs."""

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        from app.engine.kb.vector.retriever import retrieve
        from app.engine.workflow.expression import ExpressionEngine

        cfg = self.node_config or {}
        kb_ids: list[str] = cfg.get("kb_ids") or []
        if not kb_ids:
            return NodeResult(
                success=False,
                error_message="kb_search 节点未配置知识库（kb_ids 为空）",
                error_code="KB_SEARCH_NO_KB",
            )

        engine = ExpressionEngine(variables)
        query = engine.resolve_str(cfg.get("query", ""))
        if not query.strip():
            return NodeResult(
                success=False,
                error_message="kb_search 节点的 query 为空",
                error_code="KB_SEARCH_EMPTY_QUERY",
            )

        top_k = cfg.get("top_k") or settings.KB_VECTOR_TOP_K

        results: list[dict] = []
        errors: list[str] = []
        for kid in kb_ids:
            try:
                res = await retrieve(kid, query, top_k=top_k)
                for r in res:
                    results.append({"kb_id": kid, **r})
            except Exception as exc:
                errors.append(f"{kid}: {exc}")

        # If every KB failed, surface as node failure; partial results still
        # pass through so a downstream LLM can attempt to answer.
        if not results and errors:
            return NodeResult(
                success=False,
                error_message="检索全部失败：" + "; ".join(errors),
                error_code="KB_SEARCH_FAILED",
            )

        return NodeResult(
            success=True,
            output={"results": results, "query": query},
        )


__all__ = ["KbSearchNodeExecutor"]
