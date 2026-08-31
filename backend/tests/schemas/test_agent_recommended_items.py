"""推荐项（recommended_items）数量与字段校验测试。

上限 200 条（自 10 条放宽，配合管理端批量添加）。
纯 schema/model 校验，不触 DB。
"""
from __future__ import annotations

import pytest
from app.models.agent import Agent
from app.schemas.agent import AgentUpdate
from pydantic import ValidationError


def _items(n: int) -> list[dict[str, str]]:
    return [{"label": f"快捷输入 {i}", "prompt": f"prompt {i}"} for i in range(n)]


def test_agent_update_allows_200_items() -> None:
    update = AgentUpdate(name="a", recommended_items=_items(200))
    assert len(update.recommended_items) == 200


def test_agent_update_rejects_201_items() -> None:
    with pytest.raises(ValidationError):
        AgentUpdate(name="a", recommended_items=_items(201))


def test_agent_model_rejects_201_items() -> None:
    with pytest.raises(ValidationError):
        Agent(name="a", recommended_items=_items(201))


def test_recommended_item_label_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentUpdate(name="a", recommended_items=[{"label": "x" * 101, "prompt": ""}])


def test_recommended_item_prompt_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentUpdate(name="a", recommended_items=[{"label": "ok", "prompt": "x" * 501}])
