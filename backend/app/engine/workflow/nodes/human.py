"""HumanNodeExecutor — pauses execution and waits for human approval.

When a workflow reaches a Human node:
1. Task transitions to ``waiting_human`` status
2. A ``checkpoint.timeout_deadline`` is persisted
3. Execution waits for an external intervention (approve/reject/skip)
4. On timeout, the periodic ``sweep_timed_out_human_tasks`` Celery beat task
   scans the deadline and executes the configured ``timeout_action``
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from app.engine.workflow.expression import ExpressionEngine
from app.engine.workflow.node_executor import BaseNodeExecutor, NodeResult


class HumanNodeExecutor(BaseNodeExecutor):
    """Pause workflow execution for human approval.

    Config::

        {
            "title": "审批质检报告",
            "description": "请审核以下质检数据...",
            "options": ["approve", "reject"],
            "timeout_ms": 300000,        # 5 minutes
            "timeout_action": "auto_skip", # auto_approve | auto_reject | auto_skip | fail
            "assignee": "user_xxx"         # optional: specific user
        }
    """

    async def execute(self, variables: dict[str, Any]) -> NodeResult:
        raw_title = self.node_config.get("title", "人工审批")
        raw_description = self.node_config.get("description", "")

        # 解析 title/description 里的变量引用 {{node.field}}，让审批人能看到
        # 上游节点的实际输出（如诊断结果、告警详情），而非固定的字面文案。
        engine = ExpressionEngine(variables)
        title = self._resolve_to_str(engine, raw_title)
        description = self._resolve_to_str(engine, raw_description)

        # 系统固定提供 approve/reject 选项，当 options 为空时使用默认值
        options = self.node_config.get("options") or ["approve", "reject"]

        # 前端传入 timeout_minutes，后端统一转换为 ms
        timeout_ms = self.node_config.get("timeout_ms", 0)
        if not timeout_ms:
            timeout_minutes = self.node_config.get("timeout_minutes", 0)
            timeout_ms = int(timeout_minutes) * 60 * 1000 if timeout_minutes else 0

        timeout_action = self.node_config.get("timeout_action", "fail")

        logger.info(
            "human_node_waiting",
            node_id=self.node_id,
            title=title,
            timeout_ms=timeout_ms,
        )

        # Return result indicating the task needs human intervention.
        # The WorkflowEngine will detect the waiting_human status and
        # pause execution until an intervention is received.
        return NodeResult(
            success=True,
            output={
                "status": "waiting_human",
                "title": title,
                "description": description,
                "options": options,
                "timeout_ms": timeout_ms,
                "timeout_action": timeout_action,
                "node_id": self.node_id,
            },
        )

    @staticmethod
    def _resolve_to_str(engine: ExpressionEngine, raw: Any) -> str:
        """把模板值解析为字符串，供审批展示。

        - 非字符串原样返回（转 str）。
        - 字符串经 ExpressionEngine 解析变量引用；若解析出 dict/list（上游返回
          JSON 的常见情况），归一化为 JSON 文本，避免审批描述里出现 Python
          repr（单引号、True 大写）。
        """
        if not isinstance(raw, str):
            return str(raw)
        if not raw:
            return ""
        resolved = engine.resolve(raw)
        if resolved is None:
            return ""
        if isinstance(resolved, (dict, list)):
            import json

            return json.dumps(resolved, ensure_ascii=False, default=str)
        return str(resolved)
