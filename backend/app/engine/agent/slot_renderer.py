"""System prompt renderer — reads prompt_slots directly from Agent document.

Rendering order is fixed:
  role → task → constraints → context → output_format → tool_declaration (auto)

AgentNode overrides have highest priority for individual slots.

Usage::

    from app.engine.agent.slot_renderer import render_system_prompt_full

    system_text = await render_system_prompt_full(
        agent_doc,
        node_slot_overrides={"role": "You are a pirate."},
        variable_pool={"input": {"query": "Hello"}},
    )
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.models.prompt_template import SLOT_SCHEMA

# 检测字符串里 Jinja2 渲染 dict/list 时产生的 Python repr 片段
# （单引号包裹 key 的 {…} 或 […]），用于把混合模板里的 dict 变量渲染结果
# 修正为合法 JSON 文本。例：上游结果：{'branch': 'HEAVY'} → 含 JSON
_PY_DICT_REPR_RE = re.compile(r"\{['\"]")
_PY_LIST_REPR_RE = re.compile(r"\[['\"]")


def _coerce_slot_value(raw: Any) -> str:
    """把 ExpressionEngine 解析后的值归一化为合法的可读字符串。

    ExpressionEngine 对「单一变量引用」会保留原类型（可能返回 dict/list/bool
    而非字符串），直接用于判空和 f-string 拼接会导致两类问题：

    1. dict/list 是 truthy，判空通过，但 f-string 拼出的是 Python repr（单引号、
       True 大写），既不是合法 JSON 也难以阅读。
    2. 当模板含变量但变量解析失败时，resolve 可能返回 None，被判为空 → 报
       「必填 slot 缺失」，掩盖了真正的「变量解析失败」根因。

    本函数把非字符串值安全转成 JSON 文本（dict/list）或字符串（bool/number）；
    对于已经是字符串但内含 Jinja2 渲染出的 Python dict/list repr（混合模板
    场景），用 ast.literal_eval + json.dumps 修正为合法 JSON。None 视为未
    解析到内容返回空串。
    """
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False, default=str)
    if not isinstance(raw, str):
        return str(raw)
    # 混合模板：Jinja2 把内嵌的 dict/list 变量渲染成 Python repr（{'k': v}），
    # 这里尝试把其中的 repr 片段修正为合法 JSON 文本。
    if _PY_DICT_REPR_RE.search(raw) or _PY_LIST_REPR_RE.search(raw):
        return _fix_py_reprs_in_string(raw)
    return raw


def _fix_py_reprs_in_string(s: str) -> str:
    """把字符串里所有形如 Python dict/list repr 的片段转为 JSON 文本。

    用正则找出 ``{...}`` / ``[...]`` 片段，逐个用 ``ast.literal_eval`` 还原
    成 Python 对象，再用 ``json.dumps`` 序列化。解析失败的片段保留原样。
    """
    import ast

    # 匹配成对的 {...} 或 [...]（非贪婪，允许嵌套引号）
    pattern = re.compile(r"(\{[^{}]*\}|\[[^\[\]]*\])")

    def _replace(match: re.Match[str]) -> str:
        fragment = match.group(1)
        try:
            obj = ast.literal_eval(fragment)
            if isinstance(obj, (dict, list)):
                return json.dumps(obj, ensure_ascii=False, default=str)
        except (ValueError, SyntaxError):
            pass
        return fragment

    return pattern.sub(_replace, s)


async def render_system_prompt_full(
    agent_doc: dict,
    *,
    node_slot_overrides: dict[str, str] | None = None,
    variable_pool: dict[str, Any] | None = None,
    strict: bool = True,
    exclude_skill_names: set[str] | None = None,
    execution_context: str = "chat",
    response_schema: dict | None = None,
    output_schema: list[dict] | None = None,
) -> str:
    """Render the full system prompt from Agent's prompt_slots.

    Args:
        agent_doc: The Agent MongoDB document.
        node_slot_overrides: Per-node slot overrides (highest priority).
        variable_pool: Variable pool for Jinja2 ``{{var}}`` resolution.
        strict: When True (default), missing required slots raise ValueError.
            When False, missing required slots are silently skipped (for preview).
        execution_context: "chat"(默认)或 "workflow"(工作流 agent 节点,
            无人值守——工具声明去掉 Clarification/Task 段,追加自主执行规则)。
        response_schema: 工作流 agent 节点的 response 结构契约(可选,
            {type: object|array, fields: [...]} 嵌套最多两层)。配置时
            tool_declaration 追加 Output Contract 段,最终回复必须是
            符合契约的 JSON(即 response 的值)。
        output_schema: **已废弃**（旧格式 list[字段]）。仅为兼容热重载
            进程中残留的旧调用方保留——收到时转译为 response_schema 并
            打 warning 日志；新代码请勿使用。

    Returns:
        Fully assembled system prompt string.
    """
    if response_schema is None and output_schema:
        from loguru import logger

        logger.warning(
            "render_system_prompt_full: output_schema 参数已废弃，请改用 response_schema",
        )
        response_schema = {"type": "object", "fields": output_schema}
    agent_slots = agent_doc.get("prompt_slots", {})
    overrides = node_slot_overrides or {}

    # ── Resolve Jinja2 expressions in slot values ──
    # 记录「原始值含变量引用但渲染为空」的情况，用于在 strict 报错时给出精确
    # 根因（是变量解析失败，而非 slot 未配置）。
    resolved_agent_slots: dict[str, Any] = {}
    resolved_overrides: dict[str, Any] = {}
    empty_after_resolve: dict[str, str] = {}  # slot_name → 原始模板

    if variable_pool:
        from app.engine.workflow.expression import ExpressionEngine

        engine = ExpressionEngine(variable_pool)
        if agent_slots:
            for k, v in agent_slots.items():
                if isinstance(v, str):
                    resolved = engine.resolve(v)
                    coerced = _coerce_slot_value(resolved)
                    if not coerced and "{{" in v:
                        empty_after_resolve[k] = v
                    resolved_agent_slots[k] = coerced
                else:
                    resolved_agent_slots[k] = v
        if overrides:
            for k, v in overrides.items():
                if isinstance(v, str):
                    resolved = engine.resolve(v)
                    coerced = _coerce_slot_value(resolved)
                    if not coerced and "{{" in v:
                        empty_after_resolve[k] = v
                    resolved_overrides[k] = coerced
                else:
                    resolved_overrides[k] = v
    else:
        resolved_agent_slots = dict(agent_slots)
        resolved_overrides = dict(overrides)

    # ── Render each fixed slot in order ──
    # Priority: node override > agent prompt_slots
    parts: list[str] = []
    missing_required: list[tuple[str, str]] = []  # (name, label) pairs

    for slot_def in SLOT_SCHEMA:
        name = slot_def.name
        label = slot_def.label

        value: str = ""
        if name in resolved_overrides and resolved_overrides[name]:
            value = resolved_overrides[name]
        elif name in resolved_agent_slots and resolved_agent_slots[name]:
            value = resolved_agent_slots[name]

        if value:
            # 用 label 作为结构化前缀，让 LLM 理解每段语义角色
            parts.append(f"【{label}】\n{value}")
        elif slot_def.required:
            missing_required.append((name, label))

    if missing_required:
        missing_labels = [label for _, label in missing_required]
        if strict:
            # 区分根因：slot 未配置 vs 变量引用渲染为空
            # empty_after_resolve 的 key 是 slot name，用它反查
            unresolved: list[str] = []
            truly_missing: list[str] = []
            for name, label in missing_required:
                if name in empty_after_resolve:
                    unresolved.append(f"{label}={empty_after_resolve[name]}")
                else:
                    truly_missing.append(label)
            hints: list[str] = []
            if unresolved:
                hints.append(
                    f"以下 slot 含变量引用但渲染后为空（上游变量可能不存在或为空值）: {', '.join(unresolved)}"
                )
            if truly_missing:
                hints.append(f"以下 slot 未配置: {', '.join(truly_missing)}")
            raise ValueError(
                f"必填 Prompt Slot 缺失: {', '.join(missing_labels)}。"
                + (" " + "；".join(hints) if hints else "")
                + "。请在 Agent 配置或节点覆写中补充，或检查上游变量输出。"
            )
        # Non-strict mode: add placeholder for missing required slots
        for _, label in missing_required:
            parts.append(f"【{label}】\n（未配置）")

    # ── Always append tool_declaration at the end ──
    from app.engine.agent.builder import build_tool_declaration

    tool_decl = await build_tool_declaration(
        agent_doc,
        exclude_names=exclude_skill_names,
        execution_context=execution_context,
        response_schema=response_schema,
    )
    if tool_decl:
        parts.append(tool_decl)

    return "\n\n".join(parts)
