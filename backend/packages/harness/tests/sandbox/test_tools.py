"""AC7/AC8 cover: bash/read/write/edit/glob/grep 六工具委托 + 异常隔离。"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agent_flow_harness.sandbox.base import GrepMatch, Sandbox, SandboxResult
from agent_flow_harness.sandbox.context import (
    SandboxContext,
    reset_sandbox_context,
    set_sandbox_context,
)
from agent_flow_harness.sandbox.tools import bash, edit, grep, glob, read, write


def _mock_sandbox(
    exec_result: SandboxResult | None = None,
    exec_exc: Exception | None = None,
) -> MagicMock:
    sb = MagicMock(spec=Sandbox)
    sb.id = "mock"
    if exec_exc:
        sb.execute_command.side_effect = exec_exc
    else:
        sb.execute_command.return_value = exec_result or SandboxResult(
            stdout="ok", stderr="", exit_code=0
        )
    sb.read_file.return_value = "file content"
    sb.write_file.return_value = None
    sb.edit_file.return_value = "Successfully edited 'app.py' (1 replacement)."
    sb.glob.return_value = ["a.py", "b.py"]
    sb.grep.return_value = [GrepMatch(path="x.py", line_number=1, line="match")]
    return sb


def _set_ctx(sb):
    token = set_sandbox_context(SandboxContext(sandbox=sb))
    return token


@pytest.mark.asyncio
async def test_bash_delegates_to_sandbox():
    """AC7: bash 委托 sandbox.execute_command。"""
    sb = _mock_sandbox(SandboxResult(stdout="hello", stderr="", exit_code=0))
    token = _set_ctx(sb)
    try:
        result = await bash.ainvoke({"command": "echo hello"})
        assert "hello" in result
        sb.execute_command.assert_called_once_with("echo hello")
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_bash_includes_stderr_and_exit_code():
    sb = _mock_sandbox(SandboxResult(stdout="out", stderr="err", exit_code=2))
    token = _set_ctx(sb)
    try:
        result = await bash.ainvoke({"command": "x"})
        assert "out" in result
        assert "err" in result
        assert "2" in result
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_bash_exception_isolated():
    """AC8: sandbox 抛异常 → 返回错误字符串，不中断。"""
    sb = _mock_sandbox(exec_exc=RuntimeError("boom"))
    token = _set_ctx(sb)
    try:
        result = await bash.ainvoke({"command": "x"})
        assert "Error" in result
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_read_delegates():
    sb = _mock_sandbox()
    token = _set_ctx(sb)
    try:
        result = await read.ainvoke({"path": "app.py"})
        assert result == "file content"
        sb.read_file.assert_called_once_with("app.py")
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_write_delegates():
    sb = _mock_sandbox()
    token = _set_ctx(sb)
    try:
        result = await write.ainvoke({"path": "out.txt", "content": "data"})
        assert "wrote" in result.lower() or "success" in result.lower()
        sb.write_file.assert_called_once_with("out.txt", "data")
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_edit_delegates():
    """edit 委托 sandbox.edit_file（含 replace_all 透传）。"""
    sb = _mock_sandbox()
    token = _set_ctx(sb)
    try:
        result = await edit.ainvoke({
            "path": "app.py", "old_string": "a", "new_string": "b",
        })
        assert "Successfully edited" in result
        sb.edit_file.assert_called_once_with("app.py", "a", "b", replace_all=False)

        await edit.ainvoke({
            "path": "app.py", "old_string": "a", "new_string": "b",
            "replace_all": True,
        })
        sb.edit_file.assert_called_with("app.py", "a", "b", replace_all=True)
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_edit_exception_isolated():
    """AC8: sandbox.edit_file 抛异常 → 返回错误字符串，不中断。"""
    sb = _mock_sandbox()
    sb.edit_file.side_effect = ValueError("old_string not found in 'app.py'.")
    token = _set_ctx(sb)
    try:
        result = await edit.ainvoke({
            "path": "app.py", "old_string": "a", "new_string": "b",
        })
        assert "Error" in result
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_glob_delegates():
    sb = _mock_sandbox()
    token = _set_ctx(sb)
    try:
        result = await glob.ainvoke({"path": ".", "pattern": "*.py"})
        assert "a.py" in result
        sb.glob.assert_called_once_with(".", "*.py")
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_grep_delegates():
    sb = _mock_sandbox()
    token = _set_ctx(sb)
    try:
        result = await grep.ainvoke({"path": ".", "pattern": "match"})
        assert "match" in result
        sb.grep.assert_called_once_with(".", "match")
    finally:
        reset_sandbox_context(token)


@pytest.mark.asyncio
async def test_tool_without_context_returns_error():
    """未注入 sandbox context → 返回错误字符串（不 raise RuntimeError 给 LLM）。"""
    result = await bash.ainvoke({"command": "x"})
    assert "Error" in result
