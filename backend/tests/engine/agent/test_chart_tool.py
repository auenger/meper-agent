"""Tests for the render_chart tool — validation, option construction, fenced-block output."""
from __future__ import annotations

import json

import pytest
from app.engine.agent.chart_tool import _CHART_TOOLS, _validate_args, render_chart

# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_tool_registered() -> None:
    names = [t.name for t in _CHART_TOOLS]
    assert names == ["render_chart"]


# ---------------------------------------------------------------------------
# Happy path: each chart type produces a valid fenced block
# ---------------------------------------------------------------------------


def _parse_result(result: str) -> dict:
    """Extract and parse the ```echarts fenced block from a tool result."""
    assert result.startswith("已生成 "), result
    marker = "```echarts\n"
    start = result.index(marker) + len(marker)
    end = result.index("\n```", start)
    return json.loads(result[start:end])


@pytest.mark.parametrize("chart_type", ["bar", "line", "area", "scatter"])
async def test_cartesian_types(chart_type: str) -> None:
    result = await render_chart.ainvoke(
        {
            "type": chart_type,
            "title": "月度销量",
            "data": {
                "categories": ["1月", "2月", "3月"],
                "series": [
                    {"name": "线上", "data": [10, 20, 30]},
                    {"name": "线下", "data": [5, 15, 25]},
                ],
            },
        }
    )
    option = _parse_result(result)
    assert option["title"]["text"] == "月度销量"
    if chart_type == "scatter":
        assert option["xAxis"]["type"] == "value"
        assert option["yAxis"]["type"] == "value"
    else:
        assert option["xAxis"]["type"] == "category"
        assert option["yAxis"]["type"] == "value"
    assert len(option["series"]) == 2
    expected_series_type = "line" if chart_type == "area" else chart_type
    assert all(s["type"] == expected_series_type for s in option["series"])
    if chart_type == "area":
        assert all("areaStyle" in s for s in option["series"])


async def test_pie_type() -> None:
    result = await render_chart.ainvoke(
        {
            "type": "pie",
            "title": "渠道占比",
            "data": {"names": ["直销", "渠道"], "values": [60, 40]},
        }
    )
    option = _parse_result(result)
    assert option["tooltip"]["trigger"] == "item"
    assert option["series"][0]["type"] == "pie"
    assert option["series"][0]["data"] == [
        {"name": "直销", "value": 60},
        {"name": "渠道", "value": 40},
    ]


# ---------------------------------------------------------------------------
# Validation errors are friendly text, not exceptions
# ---------------------------------------------------------------------------


async def test_missing_type() -> None:
    # 工具 schema 本身要求 type/data 必填；缺参走 _validate_args 的友好错误层
    err = _validate_args({"data": {"categories": [], "series": []}})
    assert err is not None
    assert "type" in err


async def test_invalid_type_enum() -> None:
    result = await render_chart.ainvoke(
        {"type": "radar", "data": {"categories": ["a"], "series": [{"name": "s", "data": [1]}]}}
    )
    assert result.startswith("[render_chart]")
    assert "radar" in result


async def test_missing_data() -> None:
    err = _validate_args({"type": "bar"})
    assert err is not None
    assert "data" in err


async def test_series_length_mismatch() -> None:
    result = await render_chart.ainvoke(
        {
            "type": "bar",
            "data": {
                "categories": ["a", "b", "c"],
                "series": [{"name": "s", "data": [1, 2]}],
            },
        }
    )
    assert result.startswith("[render_chart]")
    assert "不一致" in result


async def test_pie_length_mismatch() -> None:
    result = await render_chart.ainvoke(
        {"type": "pie", "data": {"names": ["a", "b"], "values": [1]}}
    )
    assert result.startswith("[render_chart]")


# ---------------------------------------------------------------------------
# config passthrough
# ---------------------------------------------------------------------------


async def test_config_stack_smooth() -> None:
    result = await render_chart.ainvoke(
        {
            "type": "line",
            "data": {
                "categories": ["a", "b"],
                "series": [{"name": "s", "data": [1, 2]}],
            },
            "chart_config": {"stack": True, "smooth": True, "y_axis_name": "数量"},
        }
    )
    option = _parse_result(result)
    assert option["series"][0]["stack"] == "total"
    assert option["series"][0]["smooth"] is True
    assert option["yAxis"]["name"] == "数量"


async def test_config_horizontal_swaps_axes() -> None:
    result = await render_chart.ainvoke(
        {
            "type": "bar",
            "data": {
                "categories": ["a", "b"],
                "series": [{"name": "s", "data": [1, 2]}],
            },
            "chart_config": {"horizontal": True},
        }
    )
    option = _parse_result(result)
    # horizontal: value axis on x, category axis on y
    assert option["xAxis"]["type"] == "value"
    assert option["yAxis"]["type"] == "category"
    assert option["yAxis"]["data"] == ["a", "b"]


# ---------------------------------------------------------------------------
# Safety: option must be pure JSON (no function fields)
# ---------------------------------------------------------------------------


async def test_option_is_pure_json() -> None:
    result = await render_chart.ainvoke(
        {
            "type": "bar",
            "data": {
                "categories": ["a"],
                "series": [{"name": "s", "data": [1]}],
            },
        }
    )
    option = _parse_result(result)  # json.loads would fail on function syntax
    dumped = json.dumps(option)
    assert "function" not in dumped
    assert "=>" not in dumped
