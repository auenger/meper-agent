"""Workflow static validator — structural analysis without execution.

This module provides static analysis of Workflow definitions to detect
potential issues before execution:

- **DAG structure validation**: cycle detection, orphan nodes, start/end checks
- **Variable reference validation**: ``{{node.field}}`` expressions reference
  valid nodes that are upstream in the topological order
- **Node configuration validation**: required fields are present
- **Circular call detection**: Agent→Workflow→Agent potential cycles

Usage::

    validator = WorkflowValidator(workflow_doc)
    result = validator.validate()
    if not result.is_valid:
        for issue in result.issues:
            print(f"{issue.severity}: {issue.message}")
"""
from __future__ import annotations

# Regex to find ``{{...}}`` expressions
import re
from enum import StrEnum
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

_EXPRESSION_PATTERN = re.compile(r"\{\{(.+?)\}\}")

_RESPONSE_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESPONSE_FIELD_TYPES = {"string", "number", "boolean", "enum", "object"}


def _check_agent_response_schema(
    node_id: str, schema: Any,
) -> list[ValidationIssue]:
    """校验 agent 节点 response_schema（response 结构契约，API 返回体模型）。

    规则：type ∈ {text, object, array}（text 等价未声明）；object/array
    必须带非空 fields；字段名须为合法标识符（点号/特殊字符会破坏下游
    {{node.response.field}} 平铺引用）；enum 必须带非空 enum_values；
    嵌套最多两层（第一层 object 字段可带子 fields，第二层不可再嵌）。
    """
    issues: list[ValidationIssue] = []

    def err(message: str) -> None:
        issues.append(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="INVALID_RESPONSE_SCHEMA",
            message=message,
            node_id=node_id,
        ))

    def check_fields(fields: Any, *, layer: int, prefix: str) -> None:
        if not isinstance(fields, list) or not fields:
            err(f"response_schema.{prefix}fields 必须是非空字段列表")
            return
        for idx, f in enumerate(fields):
            if not isinstance(f, dict):
                err(f"response_schema.{prefix}fields[{idx}] 不是对象")
                continue
            name = str(f.get("name") or "").strip()
            if not name:
                err(f"response_schema.{prefix}fields[{idx}] 缺少 name")
                continue
            if not _RESPONSE_FIELD_NAME_RE.fullmatch(name):
                err(
                    f"response_schema 字段名 '{name}' 不合法（仅限字母/数字/下划线，"
                    f"且以字母或下划线开头——特殊字符会破坏 {{node.response.field}} 引用）"
                )
                continue
            ftype = str(f.get("type") or "string")
            if ftype not in _RESPONSE_FIELD_TYPES:
                err(
                    f"response_schema 字段 '{name}' 的 type '{ftype}' 不合法"
                    f"（应为 {sorted(_RESPONSE_FIELD_TYPES)} 之一）"
                )
                continue
            if ftype == "enum" and not (f.get("enum_values") or []):
                err(f"response_schema 字段 '{name}' 为 enum 类型，必须提供非空 enum_values")
            if ftype == "object":
                if layer >= 2:
                    err(f"response_schema 嵌套最多两层：'{name}' 已在第二层，不可再为 object")
                    continue
                sub_fields = f.get("fields")
                if sub_fields is not None:
                    check_fields(sub_fields, layer=layer + 1, prefix=f"{name}.")

    if not isinstance(schema, dict):
        err("response_schema 必须是对象（{type, fields}）")
        return issues

    schema_type = str(schema.get("type") or "text")
    if schema_type not in ("text", "object", "array"):
        err(f"response_schema.type '{schema_type}' 不合法（应为 text/object/array 之一）")
        return issues
    if schema_type == "text":
        return issues  # 等价未声明，零配置

    check_fields(schema.get("fields"), layer=1, prefix="")
    return issues


class ValidationSeverity(StrEnum):
    """Severity level of a validation issue."""

    ERROR = "error"      # Blocking — must fix before execution
    WARNING = "warning"  # Potential problem — should review
    INFO = "info"        # Informational


class ValidationIssue(BaseModel):
    """A single validation issue found in the workflow."""

    severity: ValidationSeverity
    code: str            # Error code, e.g. "CYCLE_DETECTED"
    message: str         # Human-readable description
    node_id: str | None = None  # Related node, if applicable
    context: dict[str, Any] = Field(default_factory=dict)  # Extra info

    def __str__(self) -> str:
        prefix = f"[{self.severity.value.upper()}]"
        location = f" (node: {self.node_id})" if self.node_id else ""
        return f"{prefix} {self.code}: {self.message}{location}"


class ValidationResult(BaseModel):
    """Result of workflow validation."""

    is_valid: bool
    """True if no ERROR-level issues were found."""

    issues: list[ValidationIssue] = Field(default_factory=list)
    """All issues found (errors, warnings, info)."""

    errors: list[ValidationIssue] = Field(default_factory=list)
    """Only ERROR-level issues."""

    warnings: list[ValidationIssue] = Field(default_factory=list)
    """Only WARNING-level issues."""

    @classmethod
    def from_issues(cls, issues: list[ValidationIssue]) -> ValidationResult:
        """Create a result from a list of issues."""
        errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
        warnings = [i for i in issues if i.severity == ValidationSeverity.WARNING]
        return cls(
            is_valid=len(errors) == 0,
            issues=issues,
            errors=errors,
            warnings=warnings,
        )


class WorkflowValidator:
    """Static validator for Workflow definitions.

    Performs structural analysis without executing the workflow.
    """

    def __init__(self, workflow_doc: dict[str, Any]) -> None:
        self.workflow = workflow_doc
        self.workflow_id = workflow_doc.get("_id", "")
        self.nodes: list[dict[str, Any]] = workflow_doc.get("nodes", [])
        self.edges: list[dict[str, Any]] = workflow_doc.get("edges", [])
        self.node_map: dict[str, dict[str, Any]] = {
            n["node_id"]: n for n in self.nodes if n.get("node_id")
        }

        # Build adjacency for cycle detection
        self._out_edges: dict[str, list[dict[str, Any]]] = {}
        self._in_edges: dict[str, list[dict[str, Any]]] = {}
        self._build_edge_index()

    def _build_edge_index(self) -> None:
        """Build adjacency lists from edges and next_nodes config."""
        # Legacy edges (for backward compat)
        for edge in self.edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src and tgt:
                self._out_edges.setdefault(src, []).append(edge)
                self._in_edges.setdefault(tgt, []).append(edge)

        # Modern next_nodes config
        for node in self.nodes:
            node_id = node.get("node_id", "")
            config = node.get("config", {})
            next_nodes = config.get("next_nodes", [])
            for next_node in next_nodes:
                if isinstance(next_node, dict):
                    tgt = next_node.get("target", "")
                else:
                    tgt = str(next_node)
                if tgt:
                    edge = {"source": node_id, "target": tgt}
                    self._out_edges.setdefault(node_id, []).append(edge)
                    self._in_edges.setdefault(tgt, []).append(edge)

            # Gateway: conditions[*].target and default_branch
            if node.get("type") == "gateway":
                for cond in config.get("conditions", []):
                    tgt = cond.get("target", "") if isinstance(cond, dict) else ""
                    if tgt:
                        edge = {"source": node_id, "target": tgt}
                        self._out_edges.setdefault(node_id, []).append(edge)
                        self._in_edges.setdefault(tgt, []).append(edge)
                default_branch = config.get("default_branch", "")
                if default_branch:
                    edge = {"source": node_id, "target": default_branch}
                    self._out_edges.setdefault(node_id, []).append(edge)
                    self._in_edges.setdefault(default_branch, []).append(edge)

            # Agent: insufficient_branch (abort_workflow 信号化的澄清分支)
            if node.get("type") == "agent":
                tgt = str(config.get("insufficient_branch") or "").strip()
                if tgt:
                    edge = {"source": node_id, "target": tgt}
                    self._out_edges.setdefault(node_id, []).append(edge)
                    self._in_edges.setdefault(tgt, []).append(edge)

            # Parallel: branches[*].start_node
            if node.get("type") == "parallel":
                for branch in config.get("branches", []):
                    tgt = branch.get("start_node", "") if isinstance(branch, dict) else ""
                    if tgt:
                        edge = {"source": node_id, "target": tgt}
                        self._out_edges.setdefault(node_id, []).append(edge)
                        self._in_edges.setdefault(tgt, []).append(edge)

    def validate(self) -> ValidationResult:
        """Run all validation checks and return the result.

        This is the main entry point for validation.
        """
        issues: list[ValidationIssue] = []

        # 1. DAG structure validation
        issues.extend(self._check_dag_structure())

        # 2. Variable reference validation
        issues.extend(self._check_variable_references())

        # 3. Node configuration validation
        issues.extend(self._check_node_configs())

        # 4. Circular call detection (cross-workflow analysis)
        # This requires async DB access, so it's done separately
        # See validate_async() for this check

        return ValidationResult.from_issues(issues)

    async def validate_async(self) -> ValidationResult:
        """Run all validation checks including async ones (cross-workflow).

        Use this when you need full validation including circular call detection.
        """
        # First, run synchronous checks
        result = self.validate()
        issues = list(result.issues)

        # Then, run async checks
        issues.extend(await self._check_circular_calls())

        return ValidationResult.from_issues(issues)

    # ── 1. DAG Structure Validation ──

    def _check_dag_structure(self) -> list[ValidationIssue]:
        """Check DAG structure: cycles, start/end nodes, orphan nodes."""
        issues: list[ValidationIssue] = []

        # Check for start node(s)
        start_nodes = [n for n in self.nodes if n.get("type") == "start"]
        if not start_nodes:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="NO_START_NODE",
                message="Workflow has no start node",
            ))
        elif len(start_nodes) > 1:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="MULTIPLE_START_NODES",
                message=f"Workflow has {len(start_nodes)} start nodes — only one will be used",
            ))

        # End node is NOT required — the engine does not depend on it.
        # No check needed here.

        # Check for cycles using DFS
        cycle = self._detect_cycle()
        if cycle:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="CYCLE_DETECTED",
                message=f"Workflow contains a cycle: {' → '.join(cycle)}",
                context={"cycle": cycle},
            ))

        # Check for orphan nodes (unreachable from start)
        reachable = self._find_reachable_nodes()
        for node in self.nodes:
            node_id = node.get("node_id", "")
            if node_id and node_id not in reachable:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="ORPHAN_NODE",
                    message=f"Node '{node_id}' is unreachable from start",
                    node_id=node_id,
                ))

        return issues

    def _detect_cycle(self) -> list[str] | None:
        """Detect cycle in DAG using DFS. Returns the cycle path if found."""
        white, gray, black = 0, 1, 2  # noqa: N806 — standard DFS color constants
        color: dict[str, int] = {n.get("node_id", ""): white for n in self.nodes}
        parent: dict[str, str | None] = {n.get("node_id", ""): None for n in self.nodes}

        def dfs(node_id: str) -> list[str] | None:
            color[node_id] = gray
            for edge in self._out_edges.get(node_id, []):
                tgt = edge.get("target", "")
                if not tgt or tgt not in color:
                    continue
                if color[tgt] == gray:
                    # Found a cycle — reconstruct the path
                    cycle = [tgt, node_id]
                    current = parent.get(node_id)
                    while current and current != tgt:
                        cycle.append(current)
                        current = parent.get(current)
                    cycle.reverse()
                    return cycle
                if color[tgt] == white:
                    parent[tgt] = node_id
                    result = dfs(tgt)
                    if result:
                        return result
            color[node_id] = black
            return None

        for node in self.nodes:
            node_id = node.get("node_id", "")
            if node_id and color.get(node_id) == white:
                result = dfs(node_id)
                if result:
                    return result
        return None

    def _find_reachable_nodes(self) -> set[str]:
        """Find all nodes reachable from start nodes using BFS."""
        start_nodes = [n.get("node_id", "") for n in self.nodes if n.get("type") == "start"]
        if not start_nodes:
            return set()

        reachable: set[str] = set()
        queue = list(start_nodes)

        while queue:
            node_id = queue.pop(0)
            if node_id in reachable:
                continue
            reachable.add(node_id)
            for edge in self._out_edges.get(node_id, []):
                tgt = edge.get("target", "")
                if tgt and tgt not in reachable:
                    queue.append(tgt)

        return reachable

    # ── 2. Variable Reference Validation ──

    def _check_variable_references(self) -> list[ValidationIssue]:
        """Check that all {{node.field}} references are valid."""
        issues: list[ValidationIssue] = []

        # Build set of valid reference sources
        valid_sources = {"input", "system"}  # Always valid
        for node in self.nodes:
            node_id = node.get("node_id", "")
            if node_id:
                valid_sources.add(node_id)

        # Check each node's config for invalid references
        for node in self.nodes:
            node_id = node.get("node_id", "")
            config = node.get("config", {})
            refs = self._extract_variable_refs(config)

            # Build set of upstream nodes (for topological validation)
            upstream = self._find_upstream_nodes(node_id)

            for ref in refs:
                # Extract the source (first part before '.')
                source = ref.split(".")[0] if "." in ref else ref

                # Check if source exists
                if source not in valid_sources:
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="INVALID_VARIABLE_REF",
                        message=f"Variable reference '{{{{{ref}}}}}' refers to non-existent source '{source}'",
                        node_id=node_id,
                        context={"reference": ref},
                    ))
                    continue

                # Check if source is upstream (not self, not downstream)
                if source not in {"input", "system", node_id} and source not in upstream:
                        issues.append(ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="FORWARD_REFERENCE",
                            message=f"Variable reference '{{{{{ref}}}}}' refers to downstream or unrelated node '{source}'",
                            node_id=node_id,
                            context={"reference": ref, "upstream": list(upstream)},
                        ))

        return issues

    def _extract_variable_refs(self, obj: Any) -> list[str]:
        """Recursively extract all variable references from a config object."""
        refs: list[str] = []

        if isinstance(obj, str):
            for match in _EXPRESSION_PATTERN.finditer(obj):
                expr = match.group(1).strip()
                # Extract the base reference (first identifier)
                # e.g. "node_id.field.subfield" → "node_id.field.subfield"
                refs.append(expr)
        elif isinstance(obj, dict):
            for value in obj.values():
                refs.extend(self._extract_variable_refs(value))
        elif isinstance(obj, list):
            for item in obj:
                refs.extend(self._extract_variable_refs(item))

        return refs

    def _find_upstream_nodes(self, node_id: str) -> set[str]:
        """Find all nodes that are upstream of the given node (can reach it)."""
        upstream: set[str] = set()
        queue = [node_id]

        while queue:
            current = queue.pop(0)
            for edge in self._in_edges.get(current, []):
                src = edge.get("source", "")
                if src and src not in upstream:
                    upstream.add(src)
                    queue.append(src)

        return upstream

    # ── 3. Node Configuration Validation ──

    def _check_node_configs(self) -> list[ValidationIssue]:
        """Check that each node has required configuration fields."""
        issues: list[ValidationIssue] = []

        for node in self.nodes:
            node_id = node.get("node_id", "")
            node_type = node.get("type", "")
            config = node.get("config", {})
            # 报错信息用可读标识：label 优先，缺省回退 node_id（裸 node_id
            # 用户难以对应到画布上的具体节点）
            node_label = str(node.get("label") or "").strip() or node_id

            if not node_id:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_NODE_ID",
                    message="Node is missing node_id",
                ))
                continue

            # Type-specific checks
            if node_type == "agent":
                if not config.get("agent_id"):
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="MISSING_AGENT_ID",
                        message=f'Agent 节点 "{node_label}" 未选择 Agent（agent_id 为空）',
                        node_id=node_id,
                    ))

                # insufficient_branch：abort_workflow 触发时的澄清分支
                # （未配置 = 诚实硬失败，现状语义）。指向不存在的节点会让
                # 执行流静默中断，按 ERROR 处理。
                insufficient_branch = str(config.get("insufficient_branch") or "").strip()
                if insufficient_branch and insufficient_branch not in self.node_map:
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="INVALID_INSUFFICIENT_BRANCH",
                        message=(
                            f"insufficient_branch 指向的节点 '{insufficient_branch}' 不存在"
                        ),
                        node_id=node_id,
                    ))
                elif insufficient_branch and insufficient_branch == node_id:
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="INVALID_INSUFFICIENT_BRANCH",
                        message="insufficient_branch 不能指向节点自身（工作流不允许回边）",
                        node_id=node_id,
                    ))

                # response_schema：response 结构契约（API 返回体模型）合法性。
                response_schema = config.get("response_schema")
                if response_schema is not None:
                    issues.extend(
                        _check_agent_response_schema(node_id, response_schema)
                    )

            elif node_type == "subflow":
                if not config.get("workflow_id"):
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="MISSING_WORKFLOW_ID",
                        message=f'Subflow 节点 "{node_label}" 未选择子工作流（workflow_id 为空）',
                        node_id=node_id,
                    ))

            elif node_type == "tool":
                if not config.get("tool_id"):
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="MISSING_TOOL_ID",
                        message=f'工具节点 "{node_label}" 未选择工具（tool_id 为空）',
                        node_id=node_id,
                    ))

            elif node_type == "gateway":
                conditions = config.get("conditions", [])
                if not conditions:
                    issues.append(ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="EMPTY_GATEWAY_CONDITIONS",
                        message="Gateway node has no conditions — will always take default path",
                        node_id=node_id,
                    ))
                else:
                    valid_operators = {"==", "!=", ">", "<", ">=", "<=", "contains", "not_contains"}
                    # 比较符号黑名单：expression 不允许内联比较，必须用 operator 字段
                    comparison_symbols = ("==", "!=", ">=", "<=", ">", "<")
                    for idx, cond in enumerate(conditions):
                        if not isinstance(cond, dict):
                            continue
                        op = cond.get("operator")
                        # operator 缺省视为合法（向后兼容默认 ==）；显式填写则校验白名单
                        if op is not None and op not in valid_operators:
                            issues.append(ValidationIssue(
                                severity=ValidationSeverity.WARNING,
                                code="INVALID_GATEWAY_OPERATOR",
                                message=(
                                    f"Gateway condition #{idx + 1} has unsupported operator "
                                    f"'{op}' (expected one of {sorted(valid_operators)})"
                                ),
                                node_id=node_id,
                            ))
                        # expression 防内联：必须是纯变量引用 {{ xxx }}，禁止内联比较符号或多表达式
                        expression = cond.get("expression", "")
                        if isinstance(expression, str) and expression:
                            expr_stripped = expression.strip()
                            has_symbol = any(sym in expr_stripped for sym in comparison_symbols)
                            is_single_var = bool(_EXPRESSION_PATTERN.fullmatch(expr_stripped))
                            if has_symbol or not is_single_var:
                                issues.append(ValidationIssue(
                                    severity=ValidationSeverity.WARNING,
                                    code="INVALID_GATEWAY_EXPRESSION",
                                    message=(
                                        f"Gateway condition #{idx + 1} 表达式必须是单一变量引用 "
                                        f"(如 {{{{ node_id.field }}}})，请勿内联比较符号，"
                                        f"比较逻辑请用「判断符」下拉选择"
                                    ),
                                    node_id=node_id,
                                ))

        return issues

    # ── 4. Circular Call Detection (Async) ──

    async def _check_circular_calls(self) -> list[ValidationIssue]:
        """Detect potential circular calls between Agent and Workflow.

        This requires async DB access to resolve Agent→Workflow references.
        """
        issues: list[ValidationIssue] = []

        try:
            from app.db.mongodb import get_database
            db = get_database()

            # Build a graph of Workflow→Agent→Workflow calls
            # Start from this workflow
            visited_workflows: set[str] = set()
            call_stack: list[tuple[str, str]] = [(self.workflow_id, "workflow")]

            while call_stack:
                entity_id, entity_type = call_stack.pop()

                if entity_type == "workflow":
                    if entity_id in visited_workflows:
                        # Already analyzed this workflow
                        continue
                    visited_workflows.add(entity_id)

                    # Load this workflow's nodes
                    if entity_id == self.workflow_id:
                        nodes = self.nodes
                    else:
                        wf_doc = await db["workflows"].find_one({"_id": entity_id})
                        if not wf_doc:
                            continue
                        nodes = wf_doc.get("nodes", [])

                    # Find Agent and Subflow nodes
                    for node in nodes:
                        node_type = node.get("type", "")
                        config = node.get("config", {})

                        if node_type == "agent":
                            agent_id = config.get("agent_id", "")
                            if agent_id:
                                call_stack.append((agent_id, "agent"))

                        elif node_type == "subflow":
                            wf_id = config.get("workflow_id", "")
                            if wf_id:
                                if wf_id == self.workflow_id:
                                    issues.append(ValidationIssue(
                                        severity=ValidationSeverity.ERROR,
                                        code="CIRCULAR_WORKFLOW_CALL",
                                        message=f"Subflow node creates circular call back to workflow '{self.workflow_id}'",
                                        node_id=node.get("node_id", ""),
                                        context={"called_workflow_id": wf_id},
                                    ))
                                else:
                                    call_stack.append((wf_id, "workflow"))

                elif entity_type == "agent":
                    # Load agent and check if it has dispatch_workflow tools
                    agent_doc = await db["agents"].find_one({"_id": entity_id})
                    if not agent_doc:
                        continue

                    # Check tools for workflow dispatch
                    tools = agent_doc.get("tools", [])
                    for tool in tools:
                        if isinstance(tool, dict):
                            tool_config = tool.get("config", {})
                            # Look for workflow_name in tool config
                            wf_name = tool_config.get("workflow_name", "")
                            if wf_name:
                                # Resolve workflow name to ID
                                wf_doc = await db["workflows"].find_one({"name": wf_name})
                                if wf_doc:
                                    wf_id = wf_doc.get("_id", "")
                                    if wf_id == self.workflow_id:
                                        issues.append(ValidationIssue(
                                            severity=ValidationSeverity.WARNING,
                                            code="POTENTIAL_CIRCULAR_CALL",
                                            message=f"Agent '{entity_id}' may dispatch back to workflow '{self.workflow_id}'",
                                            node_id=entity_id,
                                            context={"agent_id": entity_id, "workflow_name": wf_name},
                                        ))
                                    else:
                                        call_stack.append((wf_id, "workflow"))

        except Exception as exc:
            logger.warning("circular_call_check_failed", error=str(exc))
            # Don't fail validation if this check fails — it's best-effort

        return issues


# ── Convenience Functions ──


def validate_workflow(workflow_doc: dict[str, Any]) -> ValidationResult:
    """Synchronous validation of a workflow (no cross-workflow analysis).

    Use this for quick checks during workflow save/update.
    """
    validator = WorkflowValidator(workflow_doc)
    return validator.validate()


async def validate_workflow_async(workflow_doc: dict[str, Any]) -> ValidationResult:
    """Full async validation including cross-workflow circular call detection.

    Use this before executing a workflow task.
    """
    validator = WorkflowValidator(workflow_doc)
    return await validator.validate_async()
