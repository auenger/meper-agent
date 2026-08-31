"""LLM 错误转译测试 — 图片不支持错误的识别与友好提示。"""
from __future__ import annotations

from app.utils.llm_errors import looks_like_image_error, translate_llm_error


def test_recognizes_common_provider_image_errors() -> None:
    assert looks_like_image_error(
        "Invalid content type: image_url is not supported by this model"
    )
    assert looks_like_image_error("Error code: 400 - image input is not supported")
    assert looks_like_image_error("该模型不支持图片输入")


def test_ignores_unrelated_errors() -> None:
    assert not looks_like_image_error("rate limit exceeded")
    assert not looks_like_image_error("connection timeout")
    # "image" 出现但无拒绝信号 → 不误报。
    assert not looks_like_image_error("image generation finished")


def test_translate_prefixes_hint_and_keeps_original() -> None:
    raw = "Invalid content type: image_url not supported"
    out = translate_llm_error(raw)
    assert out.startswith("当前模型可能不支持图片输入")
    assert raw in out


def test_translate_passthrough_for_normal_errors() -> None:
    assert translate_llm_error("rate limit exceeded") == "rate limit exceeded"
    assert translate_llm_error("") == ""
