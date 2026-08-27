"""Content-block text extraction — 单一事实源的回归测试。

重点覆盖 GLM anthropic-compat quirk：无视 ``thinking={"type":"disabled"}``
返回单个 thinking 块，且把最终回答塞进额外 ``text`` 字段（anthropic SDK
``extra="allow"`` 放行）→ ``{"signature", "thinking", "type", "text"}``。
此前工作流 agent 节点把该 dict 原样塞进 ``output["response"]``，前端逐行
渲染出 signature/thinking 等裸字段。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.engine.harness_integration.adapters.content import (
    extract_answer_text,
    extract_thinking_text,
)

# GLM quirk 块：key 顺序与 anthropic SDK ThinkingBlock 声明序一致
# （signature → thinking → type），额外 text 字段垫底。
_GLM_QUIRK_BLOCK = {
    "signature": "b6ff287f29a4407e9b68ebee",
    "thinking": "Vague but can proceed with helpful guidance.",
    "type": "thinking",
    "text": "好的！这是最终回答。",
}


class TestExtractAnswerText:
    def test_plain_string_passthrough(self) -> None:
        assert extract_answer_text("直接回答") == "直接回答"

    def test_none_returns_empty(self) -> None:
        assert extract_answer_text(None) == ""

    def test_standard_blocks_list(self) -> None:
        content = [
            {"type": "thinking", "thinking": "先想想", "signature": "sig"},
            {"type": "text", "text": "正文回答"},
        ]
        assert extract_answer_text(content) == "正文回答"

    def test_glm_quirk_single_block_list(self) -> None:
        """GLM quirk：正文藏在 thinking 块的 text 字段 → 只取正文。"""
        assert extract_answer_text([_GLM_QUIRK_BLOCK]) == "好的！这是最终回答。"

    def test_glm_quirk_bare_dict(self) -> None:
        """单块 dict（未包 list）同样容忍。"""
        assert extract_answer_text(dict(_GLM_QUIRK_BLOCK)) == "好的！这是最终回答。"

    def test_mixed_string_blocks(self) -> None:
        assert extract_answer_text(["a", {"type": "text", "text": "b"}]) == "ab"

    def test_signature_never_leaks(self) -> None:
        result = extract_answer_text([_GLM_QUIRK_BLOCK])
        assert "signature" not in result
        assert "b6ff287f" not in result
        assert "Vague but can proceed" not in result

    def test_thinking_only_content_returns_empty(self) -> None:
        content = [{"type": "thinking", "thinking": "只有思考没有回答"}]
        assert extract_answer_text(content) == ""

    def test_unknown_type_returns_empty(self) -> None:
        assert extract_answer_text(42) == ""


class TestExtractThinkingText:
    def test_anthropic_thinking_block(self) -> None:
        msg = SimpleNamespace(content=[
            {"type": "thinking", "thinking": "推理过程", "signature": "sig"},
            {"type": "text", "text": "回答"},
        ])
        assert extract_thinking_text(msg) == "推理过程"

    def test_glm_quirk_block_thinking_field(self) -> None:
        msg = SimpleNamespace(content=[_GLM_QUIRK_BLOCK])
        assert extract_thinking_text(msg) == "Vague but can proceed with helpful guidance."

    def test_openai_reasoning_content_kwarg(self) -> None:
        msg = SimpleNamespace(
            content="回答",
            additional_kwargs={"reasoning_content": "思考"},
        )
        assert extract_thinking_text(msg) == "思考"

    def test_no_thinking_returns_empty(self) -> None:
        msg = SimpleNamespace(content="只有正文")
        assert extract_thinking_text(msg) == ""

    def test_dict_message_returns_empty(self) -> None:
        """dict 形态消息（无 additional_kwargs 属性）安全返回空。"""
        assert extract_thinking_text({"content": "回答"}) == ""
