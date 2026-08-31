"""view_image —— 内置图片回看工具，按 file_id 把历史图片重新载入上下文。

多模态图片只在注入当轮以 image_url 块存在；旧轮图片会被 harness 压缩层
降级为可回取占位（formatter 由 harness_integration.context 注入）。本工具
是"回取"的落地：按 file_id 读 FileRef → 降采样/光栅化 → 返回多模态
content blocks（LangChain ToolMessage.content 支持 list）——工具结果对
模型可见当轮，之后同样被降级，需要时再调（每字节的可见性都有价格）。

与 parse_file 同构的安全模型：workspace context 拿 user_id/session_id，
FileRef 必须属于当前用户且 origin_id == 当前 session（对话附件）——防止
工具被诱导读取任意库内文件。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool


def _err(msg: str) -> str:
    """返回友好错误文本（不抛异常，让 agent 能读懂并修正）。"""
    return f"[view_image] {msg}"


@tool
async def view_image(file_id: str) -> list[dict] | str:
    """Load an image from the chat history into context for visual inspection.

    Use when you need to look at an image again — stale images are degraded
    out of context with a placeholder like "[图片 xx 已移出上下文,可用
    view_image(file_id=...) 查看]"; this tool re-fetches the actual pixels.
    Get ``file_id`` from the <file> tag's id attribute or the [IMAGE ...]
    marker. Only images previously attached in the current chat session can
    be viewed.

    Args:
        file_id: File id from the <attachments> block / [IMAGE] marker
            (starts with "file_").
    """
    from app.core.config import settings
    from app.engine.agent.builtin_tools import _get_workspace

    workspace = _get_workspace()
    if workspace is None:
        return _err("当前无工作区上下文，无法查看图片")
    session_id = workspace.session_id
    if not session_id:
        return _err("当前非对话会话上下文，无法回看历史图片")

    from app.services.file_service import FileService
    from app.services.file_storage import LocalFileStorage

    loaded = await FileService(storage=LocalFileStorage()).load_content(file_id)
    if loaded is None:
        return _err(f"图片不存在：{file_id}")
    fref, data = loaded
    if fref.owner_user_id != workspace.user_id:
        return _err(f"文件 {file_id} 不属于当前用户，无权访问")
    if fref.origin_id != session_id:
        return _err(f"文件 {file_id} 不是当前会话的附件，无权访问")

    from app.services.file_rendering import (
        downscale_image_bytes,
        rasterize_svg,
        sniff_image_mime,
    )

    mime = sniff_image_mime(data, fref.mime_type or "")
    if not mime:
        return _err(f"文件 {fref.name} 不是图片（{fref.mime_type or '未知类型'}）")
    if mime == "image/svg+xml":
        png = rasterize_svg(data)
        if png is None:
            return _err(f"SVG 渲染失败：{fref.name}（其源码已在附件清单中）")
        data = png
        mime = "image/png"
    else:
        res = downscale_image_bytes(
            data,
            max_edge=settings.IMAGE_MAX_EDGE,
            max_size=settings.IMAGE_MAX_SIZE_BYTES,
        )
        if res is None:
            return _err(f"图片处理失败或超出大小限制：{fref.name}")
        data, mime = res

    import base64

    b64 = base64.b64encode(data).decode()
    # 标记块与 image_url 块成对——harness 降级图片时靠它反查 file_id/name,
    # 与 file_rendering.build_multimodal_content 的结构保持一致。
    # 纯文本主模型收不到图块(兼容网关静默丢弃)——图片语义转化由模型按需
    # 调用专门的多模态工具完成(平台不做自动转化,因为无法预知模型能力)。
    return [
        {"type": "text", "text": f'[IMAGE file_id="{file_id}" name="{fref.name}"]'},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


# ---------------------------------------------------------------------------
# Tool list — exported for context.py injection (builtin_config 白名单过滤)
# ---------------------------------------------------------------------------

_IMAGE_TOOLS: list[BaseTool] = [view_image]

# name → tool 实例查找表：/tools/builtin 端点、context 注入、builder prompt 声明
# 三处共用（harness BUILTIN_TOOLS 取不到 app 层工具，需补此表查找）。
IMAGE_TOOL_BY_NAME: dict[str, BaseTool] = {t.name: t for t in _IMAGE_TOOLS}
