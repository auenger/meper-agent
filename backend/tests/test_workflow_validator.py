"""Tests for WorkflowValidator — static structural analysis."""
from __future__ import annotations

from app.engine.workflow.validator import (
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    validate_workflow,
)

# ── Test Data ──


def _make_workflow(
    nodes: list[dict],
    edges: list[dict] | None = None,
) -> dict:
    """Helper to create a workflow document for testing."""
    return {
        "_id": "wf_test",
        "name": "Test Workflow",
        "nodes": nodes,
        "edges": edges or [],
    }


# ── DAG Structure Tests ──


class TestDAGStructure:
    """Test DAG structure validation."""

    def test_valid_simple_workflow(self):
        """A simple linear workflow should pass validation."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {"agent_id": "agent_xxx"}},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert result.is_valid
        assert len(result.errors) == 0

    def test_no_start_node(self):
        """Workflow without start node should fail."""
        workflow = _make_workflow([
            {"node_id": "agent1", "type": "agent", "config": {"agent_id": "agent_xxx"}},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "NO_START_NODE" for i in result.errors)

    def test_no_end_node(self):
        """Workflow without end node should be valid (end node is not required)."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {"agent_id": "agent_xxx"}},
        ])

        result = validate_workflow(workflow)
        # End node is not required — engine does not depend on it
        assert result.is_valid

    def test_cycle_detection(self):
        """Workflow with a cycle should fail."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "a"}]}},
            {"node_id": "a", "type": "agent", "config": {"agent_id": "a1", "next_nodes": [{"target": "b"}]}},
            {"node_id": "b", "type": "agent", "config": {"agent_id": "b1", "next_nodes": [{"target": "a"}]}},  # Cycle!
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "CYCLE_DETECTED" for i in result.errors)

    def test_orphan_node(self):
        """Unreachable node should generate a warning."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "end"}]}},
            {"node_id": "end", "type": "end", "config": {}},
            {"node_id": "orphan", "type": "agent", "config": {"agent_id": "agent_xxx"}},  # Unreachable
        ])

        result = validate_workflow(workflow)
        # Should still be valid (no errors), but with a warning
        assert result.is_valid
        assert any(i.code == "ORPHAN_NODE" for i in result.warnings)


# ── Variable Reference Tests ──


class TestVariableReferences:
    """Test variable reference validation."""

    def test_valid_variable_reference(self):
        """Valid {{node.field}} reference should pass."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "input_query": "{{input.user_name}}",  # Valid reference
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert result.is_valid

    def test_invalid_variable_reference(self):
        """Reference to non-existent node should fail."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "input_query": "{{nonexistent.field}}",  # Invalid!
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_VARIABLE_REF" for i in result.errors)

    def test_forward_reference(self):
        """Reference to downstream node should fail."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "input_query": "{{agent2.result}}",  # Forward reference!
                "next_nodes": [{"target": "agent2"}],
            }},
            {"node_id": "agent2", "type": "agent", "config": {"agent_id": "agent_yyy"}},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "FORWARD_REFERENCE" for i in result.errors)


# ── Node Configuration Tests ──


class TestNodeConfiguration:
    """Test node configuration validation."""

    def test_agent_missing_id(self):
        """Agent node without agent_id should fail."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {}},  # Missing agent_id!
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "MISSING_AGENT_ID" for i in result.errors)

    def test_subflow_missing_workflow_id(self):
        """Subflow node without workflow_id should fail."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "sub1"}]}},
            {"node_id": "sub1", "type": "subflow", "config": {}},  # Missing workflow_id!
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "MISSING_WORKFLOW_ID" for i in result.errors)

    def test_empty_gateway_conditions(self):
        """Gateway without conditions should warn."""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "gw1"}]}},
            {"node_id": "gw1", "type": "gateway", "config": {"conditions": []}},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert result.is_valid  # Still valid, just a warning
        assert any(i.code == "EMPTY_GATEWAY_CONDITIONS" for i in result.warnings)


class TestAgentNodeNewConfigs:
    """agent 节点新配置（insufficient_branch / output_schema）校验。"""

    def test_insufficient_branch_to_missing_node_fails(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "insufficient_branch": "ghost_node",  # 不存在
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_INSUFFICIENT_BRANCH" for i in result.errors)

    def test_insufficient_branch_to_self_fails(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "insufficient_branch": "agent1",  # 指向自身（回边）
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_INSUFFICIENT_BRANCH" for i in result.errors)

    def test_valid_insufficient_branch_passes(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "insufficient_branch": "human1",
            }},
            {"node_id": "human1", "type": "human", "config": {
                "title": "请补充信息", "next_nodes": [{"target": "end"}],
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert result.is_valid
        # insufficient_branch 计入连边索引：human1 不因无 next_nodes 入边被判孤儿
        assert not any(
            i.code == "ORPHAN_NODE" and i.node_id == "human1" for i in result.warnings
        )

    def test_response_schema_invalid_type(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "object", "fields": [
                    {"name": "status", "type": "object-typo"},
                ]},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_RESPONSE_SCHEMA" for i in result.errors)

    def test_response_schema_enum_requires_values(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "object", "fields": [
                    {"name": "status", "type": "enum"},  # 缺 enum_values
                ]},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_RESPONSE_SCHEMA" for i in result.errors)

    def test_response_schema_missing_name(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "object", "fields": [{"type": "string"}]},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_RESPONSE_SCHEMA" for i in result.errors)

    def test_response_schema_illegal_field_name(self):
        """字段名含特殊字符会破坏 {{node.response.field}} 引用。"""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "object", "fields": [
                    {"name": "bad-name", "type": "string"},
                ]},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_RESPONSE_SCHEMA" for i in result.errors)

    def test_response_schema_third_level_rejected(self):
        """嵌套最多两层：第三层 object 报错。"""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "object", "fields": [
                    {"name": "a", "type": "object", "fields": [
                        {"name": "b", "type": "object", "fields": [
                            {"name": "c", "type": "string"},
                        ]},
                    ]},
                ]},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_RESPONSE_SCHEMA" for i in result.errors)

    def test_response_schema_empty_fields_rejected(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "object", "fields": []},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "INVALID_RESPONSE_SCHEMA" for i in result.errors)

    def test_response_schema_text_is_valid(self):
        """type=text 等价未声明（零配置），合法。"""
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "text"},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert result.is_valid

    def test_response_schema_valid_nested_passes(self):
        workflow = _make_workflow([
            {"node_id": "start", "type": "start", "config": {"next_nodes": [{"target": "agent1"}]}},
            {"node_id": "agent1", "type": "agent", "config": {
                "agent_id": "agent_xxx",
                "response_schema": {"type": "object", "fields": [
                    {"name": "status", "type": "enum", "required": True,
                     "enum_values": ["completed", "insufficient_info"]},
                    {"name": "summary", "type": "string", "required": True},
                    {"name": "author", "type": "object", "fields": [
                        {"name": "name", "type": "string"},
                    ]},
                ]},
            }},
            {"node_id": "end", "type": "end", "config": {}},
        ])

        result = validate_workflow(workflow)
        assert result.is_valid


# ── Edge Cases ──


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_workflow(self):
        """Empty workflow should report missing start (error)."""
        workflow = _make_workflow([])

        result = validate_workflow(workflow)
        assert not result.is_valid
        assert any(i.code == "NO_START_NODE" for i in result.errors)

    def test_legacy_edges(self):
        """Workflow with legacy edges should be validated correctly."""
        workflow = _make_workflow(
            nodes=[
                {"node_id": "start", "type": "start", "config": {}},
                {"node_id": "agent1", "type": "agent", "config": {"agent_id": "agent_xxx"}},
                {"node_id": "end", "type": "end", "config": {}},
            ],
            edges=[
                {"source": "start", "target": "agent1"},
                {"source": "agent1", "target": "end"},
            ],
        )

        result = validate_workflow(workflow)
        assert result.is_valid


# ── Integration Test ──


class TestValidationResult:
    """Test ValidationResult model."""

    def test_from_issues(self):
        """Test creating result from issues."""
        issues = [
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="TEST_ERROR",
                message="Test error",
            ),
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="TEST_WARNING",
                message="Test warning",
            ),
        ]

        result = ValidationResult.from_issues(issues)
        assert not result.is_valid
        assert len(result.errors) == 1
        assert len(result.warnings) == 1
        assert len(result.issues) == 2

    def test_issue_str(self):
        """Test ValidationIssue string representation."""
        issue = ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code="TEST_CODE",
            message="Test message",
            node_id="node_1",
        )

        s = str(issue)
        assert "ERROR" in s
        assert "TEST_CODE" in s
        assert "node_1" in s
