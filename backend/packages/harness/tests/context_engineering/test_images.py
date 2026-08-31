"""Tests for stale-image downgrade — 旧图降级为可回取占位。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agent_flow_harness.context_engineering.images import downgrade_stale_images


def _fmt(fid: str, name: str) -> str:
    return f'[图片 {name}(file_id="{fid}")已移出,可用 view_image(file_id="{fid}") 查看]'


def _img_msg(mid: str, fid: str, name: str = "a.png") -> HumanMessage:
    """构造带 [IMAGE 标记]+image_url 对的多模态 HumanMessage。"""
    content: list = [{"type": "text", "text": f"see image {fid}"}]
    content.append({"type": "text", "text": f'[IMAGE file_id="{fid}" name="{name}"]'})
    content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,QQ==:{fid}"}})
    return HumanMessage(content=content, id=mid)


def _turns(n: int, start: int = 0) -> list:
    """生成 n 个轮次(每轮 Human+AI 回复)。"""
    msgs: list = []
    for i in range(start, start + n):
        msgs.append(HumanMessage(content=f"h{i}", id=f"h{i}"))
        msgs.append(HumanMessage(content=f"ai{i}", id=f"a{i}"))
    return msgs


def test_downgrades_stale_image_with_marker() -> None:
    """protected 外的 image 块降级为 formatter 文本,标记块被移除。"""
    messages: list = [
        SystemMessage(content="sys", id="sys"),
        *_turns(1),
        _img_msg("m1", "f1", "shot.png"),
        *_turns(8, start=10),  # 后置轮次把图片推出保护窗口
    ]
    result, count = downgrade_stale_images(
        messages, protected_turns=2, keep_recent=0, formatter=_fmt,
    )
    assert count == 1
    # 找到降级后的消息
    m1 = next(m for m in result if getattr(m, "id", "") == "m1")
    assert isinstance(m1.content, list)
    kinds = [b.get("type") for b in m1.content]
    assert "image_url" not in kinds
    texts = [b.get("text", "") for b in m1.content if b.get("type") == "text"]
    assert any("f1" in t and "view_image" in t for t in texts)
    # 标记块本身已被占位替代(不再出现 [IMAGE file_id="f1"])
    assert not any('[IMAGE file_id="f1"' in t for t in texts)


def test_keep_recent_exempts_latest_images() -> None:
    """keep_recent=N 豁免降级候选中最近 N 张。"""
    messages: list = [
        SystemMessage(content="sys", id="sys"),
        _img_msg("m1", "f1"),
        _img_msg("m2", "f2"),
        *_turns(8),
    ]
    result, count = downgrade_stale_images(
        messages, protected_turns=1, keep_recent=1, formatter=_fmt,
    )
    assert count == 1
    m1 = next(m for m in result if getattr(m, "id", "") == "m1")
    m2 = next(m for m in result if getattr(m, "id", "") == "m2")
    assert not any(b.get("type") == "image_url" for b in m1.content)
    assert any(b.get("type") == "image_url" for b in m2.content)  # 豁免保留


def test_tool_message_images_also_downgrade() -> None:
    """ToolMessage(view_image 结果)的 image 块同样按标记降级。"""
    content: list = [
        {"type": "text", "text": '[IMAGE file_id="fx" name="b.jpg"]'},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QQ=="}},
    ]
    messages: list = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="h0", id="h0"),
        ToolMessage(content=content, tool_call_id="tc1"),
        *_turns(8),
    ]
    result, count = downgrade_stale_images(
        messages, protected_turns=2, keep_recent=0, formatter=_fmt,
    )
    assert count == 1
    tm = next(m for m in result if isinstance(m, ToolMessage))
    assert not any(b.get("type") == "image_url" for b in tm.content)
    assert any("fx" in b.get("text", "") for b in tm.content if b.get("type") == "text")


def test_unmarked_image_left_untouched() -> None:
    """无 [IMAGE 标记] 配对的 image 块保守跳过(不破坏)。"""
    content: list = [
        {"type": "text", "text": "bare"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
    ]
    messages: list = [
        SystemMessage(content="sys", id="sys"),
        HumanMessage(content="h0", id="h0"),
        HumanMessage(content=content, id="m9"),
        *_turns(8),
    ]
    result, count = downgrade_stale_images(
        messages, protected_turns=2, keep_recent=0, formatter=_fmt,
    )
    assert count == 0
    assert result is messages


def test_no_outer_history_noop() -> None:
    """全部消息都在 protected 窗口内 → 原样返回。"""
    messages: list = [
        SystemMessage(content="sys", id="sys"),
        _img_msg("m1", "f1"),
    ]
    result, count = downgrade_stale_images(
        messages, protected_turns=5, keep_recent=0, formatter=_fmt,
    )
    assert count == 0
    assert result is messages

