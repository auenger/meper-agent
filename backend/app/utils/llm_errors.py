"""LLM 错误消息转译 — 供 SSE 事件与异常两条路径共用。

多模态图片消息发到不支持 vision 的模型时，provider 返回的错误各式各样
（"Invalid content type"、"image input is not supported"、"does not support
image"…）。这里按关键词识别并前缀友好提示（原文附后），让终端用户能直接
理解"换个模型或去掉图片"，而不是面对一段 provider 原始报错。

设计取舍：平台是 BYO-model（自定义 base_url + model），无法可靠预知模型
是否支持 vision —— 因此不做能力声明，靠报错兜底转译。
"""
from __future__ import annotations

# "不支持图片输入"类错误的关键词（小写匹配，覆盖 OpenAI/Anthropic/GLM 兼容
# 网关的常见报错文案）。
_IMAGE_ERROR_KEYWORDS = (
    "image", "multimodal", "multi-modal", "modality", "vision",
    "visual", "不支持图片", "图片输入",
)

_IMAGE_HINT = (
    "当前模型可能不支持图片输入，请切换到视觉模型（如 GLM-4V/GPT-4o/Claude）"
    "或移除图片后重试。"
)


def looks_like_image_error(message: str) -> bool:
    """判断错误消息是否疑似"模型不支持图片输入"。

    需要"图片类关键词"与"拒绝/不支持类信号"同时命中，避免
    "image generation tool failed" 这类误报。
    """
    msg = (message or "").lower()
    if not any(kw in msg for kw in _IMAGE_ERROR_KEYWORDS):
        return False
    reject_signals = (
        "not support", "unsupported", "invalid content", "invalid type",
        "invalid_request", "does not", "cannot", "无法", "不支持", "失败",
        "error", "unexpected",
    )
    return any(sig in msg for sig in reject_signals)


def translate_llm_error(message: str) -> str:
    """疑似图片不支持错误 → 前缀友好提示（原文附后）；其余原样返回。"""
    if message and looks_like_image_error(message):
        return f"{_IMAGE_HINT}\n原始错误：{message}"
    return message or ""
