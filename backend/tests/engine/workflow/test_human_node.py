"""Tests for HumanNodeExecutor — focus on the approval view config passthrough.

HumanNodeExecutor is a thin executor that returns a ``waiting_human`` signal for
the engine to act on. The key contract we lock down here:

1. ``config.view`` is passed through to ``output.view`` unchanged (variable
   references are NOT resolved — the frontend resolves them against
   ``task.variables``).
2. When ``view`` is absent, ``output`` does not carry a ``view`` key that would
   accidentally trigger the frontend's ApprovalView rendering (falsy / None is
   fine — the frontend checks ``sections?.length``).
3. ``title`` / ``description`` still support ``{{node.field}}`` resolution.
4. ``timeout_minutes`` (from frontend) → ``timeout_ms`` conversion.
"""
from __future__ import annotations

import pytest
from app.engine.workflow.nodes.human import HumanNodeExecutor


def _make(node_id: str = "human_1", config: dict | None = None) -> HumanNodeExecutor:
    return HumanNodeExecutor(node_id=node_id, node_config=config or {})


@pytest.mark.asyncio
async def test_view_passthrough_unchanged():
    """config.view is passed through to output.view without resolution."""
    view = {
        "sections": [
            {"type": "fields", "title": "申请信息", "items": [{"label": "金额", "source": "{{input.amount}}"}]},
            {"type": "document", "title": "报告", "source": "{{agent.report}}"},
        ]
    }
    executor = _make(config={"title": "审批", "view": view})
    result = await executor.execute({"input": {"amount": 100}})

    assert result.success
    assert result.output["status"] == "waiting_human"
    # view is passed through verbatim — variables NOT resolved (frontend resolves)
    assert result.output["view"] == view
    assert result.output["view"]["sections"][0]["items"][0]["source"] == "{{input.amount}}"


@pytest.mark.asyncio
async def test_no_view_when_not_configured():
    """When config has no view, output.view should be falsy (no sections)."""
    executor = _make(config={"title": "审批"})
    result = await executor.execute({})

    assert result.success
    assert not result.output.get("view")


@pytest.mark.asyncio
async def test_title_description_resolve_variables():
    """title/description resolve {{node.field}} references for display."""
    executor = _make(config={
        "title": "审批：{{input.topic}}",
        "description": "金额：{{input.amount}}",
    })
    result = await executor.execute({"input": {"topic": "差旅报销", "amount": 3500}})

    assert result.output["title"] == "审批：差旅报销"
    assert result.output["description"] == "金额：3500"


@pytest.mark.asyncio
async def test_timeout_minutes_to_ms_conversion():
    """timeout_minutes (frontend) is converted to timeout_ms."""
    executor = _make(config={"timeout_minutes": 5})
    result = await executor.execute({})

    assert result.output["timeout_ms"] == 5 * 60 * 1000


@pytest.mark.asyncio
async def test_timeout_ms_takes_precedence():
    """If timeout_ms is set directly, it takes precedence over timeout_minutes."""
    executor = _make(config={"timeout_ms": 10000, "timeout_minutes": 5})
    result = await executor.execute({})

    assert result.output["timeout_ms"] == 10000


@pytest.mark.asyncio
async def test_default_options_when_unconfigured():
    """options defaults to approve/reject when not set."""
    executor = _make(config={})
    result = await executor.execute({})

    assert result.output["options"] == ["approve", "reject"]


@pytest.mark.asyncio
async def test_default_timeout_action_is_fail():
    """timeout_action defaults to 'fail'."""
    executor = _make(config={})
    result = await executor.execute({})

    assert result.output["timeout_action"] == "fail"


@pytest.mark.asyncio
async def test_description_pure_ref_dict_serialized_to_json():
    """When description is a pure {{ref}} that resolves to a dict, it's JSON-serialized.

    Note: mixed text like 'data: {{ref}}' goes through Jinja2 string rendering,
    which produces Python repr for dicts. Only a *pure* reference preserves the
    dict and triggers JSON serialization in _resolve_to_str.
    """
    executor = _make(config={"description": "{{input.payload}}"})
    result = await executor.execute({"input": {"payload": {"key": "value"}}})

    # Pure ref → dict → JSON serialization (no Python single-quote repr)
    assert '"key"' in result.output["description"]
    assert "'key'" not in result.output["description"]
