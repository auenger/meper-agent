"""render_chart —— 内置图表工具，构造合法 echarts option 供前端渲染。

agent 调用结构化参数（type/data/title/config）→ 构造 echarts option dict →
包成 ```echarts fenced block 返回（前端 markdown 渲染点识别 language=echarts
即出图）。option 由代码构造，结构天然合法，是「option 高风险输入」的根因治理。

支持的图表类型：bar / line / area / pie / scatter。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, tool

# 合法图表类型（映射到 echarts series.type；area 复用 line + areaStyle）
VALID_TYPES = {"bar", "line", "area", "pie", "scatter"}


def _err(msg: str) -> str:
    """返回友好错误文本（不抛异常，让 agent 能读懂并修正）。"""
    return f"[render_chart] {msg}"


def _validate_args(args: dict) -> str | None:
    """校验 args，返回错误文本或 None（通过）。"""
    if not isinstance(args, dict):
        return _err("参数必须是对象")

    chart_type = args.get("type")
    if not chart_type:
        return _err("缺少必填参数 type（bar/line/area/pie/scatter）")
    if chart_type not in VALID_TYPES:
        return _err(f"type 非法：{chart_type}（合法值：{sorted(VALID_TYPES)}）")

    data = args.get("data")
    if not isinstance(data, dict):
        return _err("缺少必填参数 data（对象）")

    if chart_type == "pie":
        names = data.get("names")
        values = data.get("values")
        if not isinstance(names, list) or not isinstance(values, list):
            return _err("pie 类型的 data 需含 names:[] 与 values:[]（数组）")
        if len(names) != len(values):
            return _err("pie 的 names 与 values 长度不一致")
        if len(names) == 0:
            return _err("pie 的 data 为空（names/values 至少 1 项）")
    else:
        categories = data.get("categories")
        series = data.get("series")
        if not isinstance(categories, list):
            return _err(f"{chart_type} 类型的 data 需含 categories:[]（数组）")
        if not isinstance(series, list) or len(series) == 0:
            return _err(f"{chart_type} 类型的 data 需含 series:[]（至少 1 条）")
        for i, s in enumerate(series):
            if not isinstance(s, dict):
                return _err(f"series[{i}] 必须是对象")
            if not s.get("name"):
                return _err(f"series[{i}] 缺少 name")
            if not isinstance(s.get("data"), list):
                return _err(f"series[{i}] 缺少 data（数组）")
            if len(categories) > 0 and len(s["data"]) != len(categories):
                return _err(
                    f"series[{i}].data 长度（{len(s['data'])}）与 categories（{len(categories)}）不一致"
                )

    config = args.get("config")
    if config is not None and not isinstance(config, dict):
        return _err("config 必须是对象（可选）")
    return None


def _build_option(args: dict) -> dict[str, Any]:
    """根据 args 构造 echarts option dict（颜色留给前端主题）。"""
    chart_type: str = args["type"]
    data: dict = args["data"]
    title: str | None = args.get("title")
    config: dict = args.get("config") or {}

    x_axis_name = config.get("x_axis_name", "")
    y_axis_name = config.get("y_axis_name", "")
    stack = bool(config.get("stack", False))
    horizontal = bool(config.get("horizontal", False))
    smooth = bool(config.get("smooth", False))

    option: dict[str, Any] = {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
    }

    if title:
        option["title"] = {"text": title, "left": "center"}

    if chart_type == "pie":
        names = data["names"]
        values = data["values"]
        option["tooltip"] = {"trigger": "item"}
        option["legend"] = {"orient": "vertical", "left": "left"}
        option["series"] = [
            {
                "name": title or "series",
                "type": "pie",
                "radius": "60%",
                "data": [{"name": n, "value": v} for n, v in zip(names, values, strict=False)],
            }
        ]
        return option

    # bar / line / area / scatter —— 直角坐标系
    categories = data["categories"]
    series_in = data["series"]

    series_type = "line" if chart_type == "area" else chart_type
    series_out = []
    for s in series_in:
        item: dict[str, Any] = {
            "name": s["name"],
            "type": series_type,
            "data": s["data"],
        }
        if chart_type == "area":
            item["areaStyle"] = {}
        if stack:
            item["stack"] = "total"
        if chart_type in ("line", "area") and smooth:
            item["smooth"] = True
        series_out.append(item)

    option["legend"] = {"top": "bottom"}
    option["series"] = series_out

    # scatter：x 轴用 value（连续），其余用 category
    if chart_type == "scatter":
        option["xAxis"] = {"type": "value", "name": x_axis_name}
        option["yAxis"] = {"type": "value", "name": y_axis_name}
    else:
        x_axis = {"type": "category", "data": categories}
        y_axis = {"type": "value"}
        if x_axis_name:
            x_axis["name"] = x_axis_name
        if y_axis_name:
            y_axis["name"] = y_axis_name
        if horizontal:
            option["xAxis"] = y_axis
            option["yAxis"] = x_axis
        else:
            option["xAxis"] = x_axis
            option["yAxis"] = y_axis

    return option


@tool
async def render_chart(
    type: str,
    data: dict[str, Any],
    title: str = "",
    chart_config: dict[str, Any] | None = None,
) -> str:
    """Generate an echarts chart (bar/line/area/pie/scatter) rendered inline in chat.

    Use when visualizing data helps: comparisons, trends, proportions, or
    scatter correlations. Pass structured data — do NOT write the option JSON
    to a file or call write_to_output afterwards; the chart renders directly
    from this tool's result.

    Args:
        type: Chart type — one of bar/line/area/pie/scatter.
        data: Chart data. For pie: {names: string[], values: number[]}.
            For others: {categories: string[], series: [{name: string, data: array}]}.
        title: Optional chart title.
        chart_config: Optional extras: x_axis_name/y_axis_name/stack/horizontal/smooth.
            (Named chart_config — a plain `config` param collides with langchain's
            internal RunnableConfig plumbing and gets dropped.)
    """
    args: dict[str, Any] = {"type": type, "data": data}
    if title:
        args["title"] = title
    if chart_config is not None:
        args["config"] = chart_config

    err = _validate_args(args)
    if err:
        return err

    option = _build_option(args)
    label = title or type

    # 双路径汇流：返回 fenced block 供前端 markdown 渲染点识别 language=echarts 出图，
    # 前面的摘要文本供 LLM 上下文 / tool_result 区展示。
    option_json = json.dumps(option, ensure_ascii=False)
    return f"已生成 {type} 图：{label}\n\n```echarts\n{option_json}\n```"


# ---------------------------------------------------------------------------
# Tool list — exported for context.py injection (always-on, like _TASK_TOOLS)
# ---------------------------------------------------------------------------

_CHART_TOOLS: list[BaseTool] = [render_chart]
