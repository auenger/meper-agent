"""多模态工具结果规范化 — image 块拆到紧随的 user 消息。

OpenAI 官方 API 支持 tool message 的 image parts,但多数兼容网关(智谱/
通义等)只处理 user 消息里的 image_url,tool message 里的被静默丢弃——
模型只收到 text 块(view_image 现场现象)。tool_wrapper 把 image 块 +
[IMAGE 标记] 拆成 follow-up HumanMessage(Command update),ToolMessage
保持纯文本。
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agent_flow_harness.graph.builder import build_agent_graph


def _image_tool() -> StructuredTool:
    async def _view(file_id: str):  # noqa: ANN202
        return [
            {"type": "text", "text": f'[IMAGE file_id="{file_id}" name="a.png"]'},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
        ]

    return StructuredTool.from_function(
        _view, name="view_image", description="view an image", coroutine=_view,
    )


@pytest.mark.asyncio
async def test_multimodal_tool_result_split_to_user_message(
    agent_doc: dict, base_state, fake_llm_factory, make_run_config
) -> None:
    """view_image 的 blocks 结果:ToolMessage 纯文本 + 紧随 HumanMessage 带图。"""
    tool = _image_tool()
    llm = fake_llm_factory([
        AIMessage(content="", tool_calls=[
            {"name": "view_image", "args": {"file_id": "f1"}, "id": "c1"},
        ]),
        AIMessage(content="这是一张红色方块图"),
    ])
    config = make_run_config(llm, tools=[tool])
    graph = build_agent_graph(agent_doc, tools=[tool], middleware=[])

    result = await graph.ainvoke(base_state, config=config)

    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    # ToolMessage 纯文本(标记块留在文本里,或空时占位)
    assert isinstance(tool_msgs[0].content, str)
    assert "QQ==" not in tool_msgs[0].content

    # 紧随其后的 HumanMessage 携带 [IMAGE 标记]+image 块
    msgs = result["messages"]
    tm_idx = next(i for i, m in enumerate(msgs) if isinstance(m, ToolMessage))
    follow = msgs[tm_idx + 1]
    assert isinstance(follow, HumanMessage)
    assert isinstance(follow.content, list)
    kinds = [b.get("type") for b in follow.content]
    assert kinds == ["text", "image_url"]
    assert '[IMAGE file_id="f1"' in follow.content[0]["text"]

    # REACT 循环正常收尾(LLM 看到图后文本回复)
    assert result["messages"][-1].content == "这是一张红色方块图"


@pytest.mark.asyncio
async def test_plain_tool_result_untouched(
    agent_doc: dict, base_state, fake_llm_factory, make_run_config
) -> None:
    """纯文本工具结果不受多模态规范化影响(原样返回)。"""
    def _plain(**_kwargs):  # noqa: ANN202
        return "plain result"

    tool = StructuredTool.from_function(_plain, name="plain", description="p")
    llm = fake_llm_factory([
        AIMessage(content="", tool_calls=[{"name": "plain", "args": {}, "id": "c1"}]),
        AIMessage(content="done"),
    ])
    config = make_run_config(llm, tools=[tool])
    graph = build_agent_graph(agent_doc, tools=[tool], middleware=[])

    result = await graph.ainvoke(base_state, config=config)

    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "plain result"
    # 没有注入额外的 HumanMessage
    human_after_tool = [
        msgs for msgs in [result["messages"]]
        for i, m in enumerate(msgs)
        if isinstance(m, ToolMessage) and i + 1 < len(msgs)
        and isinstance(msgs[i + 1], HumanMessage)
    ]
    assert not human_after_tool
