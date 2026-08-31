"""旧图片降级 — 把 protected_turns 之外的 image 块移出上下文,换可回取占位。

多模态图片注入当轮后,harness 压缩层将旧轮 image_url 块替换为应用层
formatter 生成的占位文本(携带 file_id,LLM 可用回看工具随时拉回)。这与
``tool_output`` 的"压缩可逆"哲学同构:字节始终在应用层存储,上下文里
只留引用。

为什么独立于 token 阈值:图片在**每轮** LLM 调用都真实计费视觉 token
(provider 按图计费,与文本 token 无关),estimate 的固定估值可能长期
不达压缩阈值,但每张旧图都在持续烧钱——所以降级在阈值判断之前执行。

结构约定:注入方(app 层 file_rendering / view_image 工具)保证 image_url
块紧邻其前有一个文本标记块 ``[IMAGE file_id="..." name="..."]``,降级时
从标记反查 file_id/name。解析失败的 image 块保守跳过(不破坏)。
"""
from __future__ import annotations

import re
from typing import Any, Callable

ImageReferenceFormatter = Callable[[str, str], str]

_IMAGE_MARKER_RE = re.compile(
    r'\[IMAGE file_id="(?P<fid>[^"]*)" name="(?P<name>[^"]*)"\]'
)


def _marker_file_id(text: str) -> tuple[str, str] | None:
    """解析 [IMAGE file_id=".." name=".."] 标记 → (file_id, name)。"""
    m = _IMAGE_MARKER_RE.search(text or "")
    if m is None:
        return None
    return m.group("fid"), m.group("name")


def _rewrite_message_content(
    content: list[Any],
    degrade_indexes: set[int],
    formatter: ImageReferenceFormatter,
) -> tuple[list[Any] | None, int]:
    """按块索引降级 content 里的 image_url 块。

    被降级 image 块替换为 formatter(file_id, name) 文本,其前邻的
    [IMAGE 标记] 块同时移除(信息已并入占位文本)。豁免的标记/图原样保留。
    返回 (新 content 或 None=无变化, 实际降级张数)。
    """
    new_blocks: list[Any] = []
    degraded = 0
    pending_marker: dict[str, Any] | None = None  # 尚未配对的 IMAGE 标记块
    for i, block in enumerate(content):
        if not isinstance(block, dict):
            new_blocks.append(block)
            continue
        btype = block.get("type", "")
        if btype == "text" and _IMAGE_MARKER_RE.search(str(block.get("text", ""))):
            # 标记块:先暂存,若紧随的 image 被降级则一并移除。
            pending_marker = block
            continue
        if btype == "image_url" and i in degrade_indexes:
            info = _marker_file_id(str(pending_marker.get("text", ""))) if pending_marker else None
            if info is None:
                # 无标记可反查:保守跳过(保留原图块与可能存在的标记)。
                if pending_marker is not None:
                    new_blocks.append(pending_marker)
                new_blocks.append(block)
            else:
                fid, name = info
                new_blocks.append({"type": "text", "text": formatter(fid, name)})
                degraded += 1
            pending_marker = None
            continue
        # 非降级路径:先冲刷未配对标记,再保留当前块。
        if pending_marker is not None:
            new_blocks.append(pending_marker)
            pending_marker = None
        new_blocks.append(block)
    if pending_marker is not None:
        new_blocks.append(pending_marker)
    if degraded == 0:
        return None, 0
    return new_blocks, degraded


def downgrade_stale_images(
    messages: list[Any],
    *,
    protected_turns: int,
    keep_recent: int = 0,
    formatter: ImageReferenceFormatter,
) -> tuple[list[Any], int]:
    """把 protected_turns 之外的 image 块降级为可回取占位。

    keep_recent:在降级候选中豁免最近 N 张(0 = 全部降级)。
    返回 (新消息列表, 降级张数);无变化时返回原列表引用。
    """
    from agent_flow_harness.context_engineering.split import split_system_history
    from agent_flow_harness.context_engineering.turns import split_by_turns

    system_msgs, history = split_system_history(messages)
    outer, recent = split_by_turns(history, protected_turns)
    if not outer:
        return messages, 0

    # 收集 outer 中的 image 块位置:(消息序号, 块索引)。
    candidates: list[tuple[int, int]] = []
    for mi, m in enumerate(outer):
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image_url":
                candidates.append((mi, bi))
    if not candidates:
        return messages, 0

    # 豁免最近 N 张(按出现顺序从后往前)。
    exempt = set(candidates[len(candidates) - keep_recent:]) if keep_recent > 0 else set()
    by_message: dict[int, set[int]] = {}
    for mi, bi in candidates:
        if (mi, bi) not in exempt:
            by_message.setdefault(mi, set()).add(bi)
    if not by_message:
        return messages, 0

    total = 0
    new_outer: list[Any] = []
    for mi, m in enumerate(outer):
        if mi not in by_message:
            new_outer.append(m)
            continue
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        # content 理论上是 blocks 列表；None/字符串等异常形态直接跳过（无图片块可降级）
        if not isinstance(content, list):
            new_outer.append(m)
            continue
        new_content, degraded = _rewrite_message_content(content, by_message[mi], formatter)
        total += degraded
        if new_content is None:
            new_outer.append(m)
            continue
        if isinstance(m, dict):
            rebuilt = dict(m)
            rebuilt["content"] = new_content
            new_outer.append(rebuilt)
        else:
            new_outer.append(m.model_copy(update={"content": new_content}))

    if total == 0:
        return messages, 0
    return [*system_msgs, *new_outer, *recent], total


__all__ = ["downgrade_stale_images"]
