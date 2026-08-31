"""ExecutionRequest / ExtInvokeRequest 附件轮次校验测试。

input/message 允许为空(仅附件轮次),但三者全空必须拒绝。
"""
from __future__ import annotations

import pytest
from app.schemas.execution import ExecutionRequest
from app.schemas.ext_api import ExtInvokeRequest
from pydantic import ValidationError


def test_execution_request_allows_empty_input_with_files() -> None:
    req = ExecutionRequest(input="", file_ids=["f1"])
    assert req.input == ""
    req2 = ExecutionRequest(input="", file_paths=["input/a.png"])
    assert req2.file_paths == ["input/a.png"]


def test_execution_request_rejects_all_empty() -> None:
    with pytest.raises(ValidationError, match="至少提供其一"):
        ExecutionRequest(input="")
    with pytest.raises(ValidationError, match="至少提供其一"):
        ExecutionRequest(input="   \n  ")


def test_execution_request_normal_input_still_works() -> None:
    assert ExecutionRequest(input="你好").input == "你好"


def test_ext_invoke_request_same_semantics() -> None:
    assert ExtInvokeRequest(message="", file_ids=["f1"]).message == ""
    with pytest.raises(ValidationError, match="至少提供其一"):
        ExtInvokeRequest(message="")
