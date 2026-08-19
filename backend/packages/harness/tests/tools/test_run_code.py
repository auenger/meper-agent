"""run_code 工具单测 — 执行引擎 / 工具桥接 / 受限环境 / 自愈错误消息。

覆盖核心行为：
- tools.call 桥接（工作线程同步外观 → 事件循环异步执行）
- call_many 并发与部分失败降级（``{"__error__": ...}``）
- ContextVar 在内层调用可见（MCP 凭证透传的前提）
- 受限模式 import/builtins 白名单拦截
- 单调用超时、整体超时、stdout 截断
- 多工具依赖链 + 条件分支编排（EXAMPLE_CODE 真实执行，防文档漂移）
- unknown tool 错误带 available + difflib 近似名（自愈层）
"""
from __future__ import annotations

import asyncio
import contextvars
import json

import pytest
from langchain_core.tools import StructuredTool

from agent_flow_harness.tools.run_code import (
    EXAMPLE_CODE,
    _normalize_result,
    _safe_import,
    run_code,
)
from agent_flow_harness.tools.tool_bridge_context import (
    ToolBridgeContext,
    reset_tool_bridge_context,
    set_tool_bridge_context,
)

# 测试用的凭证 ContextVar — 验证内层工具调用能读到外层设置的值。
_test_secret: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_test_secret", default=None
)


# ── Fake 工具（参数 schema 由函数签名自然推断）────────────────────────


def _make_tool(name: str, fn) -> StructuredTool:
    return StructuredTool.from_function(
        fn, name=name, description=f"fake tool {name}", coroutine=fn,
    )


async def _list_users(department: str = "") -> str:
    secret = _test_secret.get()
    # 包装结构(贴近真实 API),顺带验证 ContextVar 在内层调用可见。
    return json.dumps({
        "users": [
            {"id": "u1", "name": "alice", "status": "active"},
            {"id": "u2", "name": "bob", "status": "inactive"},
            {"id": "u3", "name": "carol", "status": "active"},
        ],
        "_secret_seen": bool(secret),
    })


async def _get_expense(user_id: str = "") -> str:
    amounts = {"u1": 20000, "u3": 500}
    return json.dumps({"user_id": user_id, "amount": amounts.get(user_id, 0)})


async def _notify_manager(names: list[str] | None = None, topic: str = "") -> str:
    return json.dumps({"notified": names or [], "topic": topic})


@pytest.fixture
def oa_tools() -> dict[str, StructuredTool]:
    """模拟一个 OA MCP:三个工具构成 依赖链 → 扇出 → 条件副作用。"""
    return {
        "mcp__oa__list_users": _make_tool("mcp__oa__list_users", _list_users),
        "mcp__oa__get_expense": _make_tool("mcp__oa__get_expense", _get_expense),
        "mcp__oa__notify_manager": _make_tool("mcp__oa__notify_manager", _notify_manager),
    }


@pytest.fixture
def bridge(oa_tools: dict[str, StructuredTool]):
    ctx = ToolBridgeContext(
        tools_map=oa_tools,
        restricted=True,
        call_timeout=5.0,
        overall_timeout=10.0,
        max_output_bytes=50_000,
    )
    token = set_tool_bridge_context(ctx)
    yield ctx
    reset_tool_bridge_context(token)


async def _run(code: str) -> str:
    """经 ainvoke 走真实调用路径(含 args 校验),返回结果文本。"""
    return await run_code.ainvoke({"code": code})  # type: ignore[arg-type]


# ── 基础桥接 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_returns_parsed_json(bridge) -> None:
    out = await _run('r = tools.call("mcp__oa__list_users")\nprint(type(r).__name__, len(r["users"]))')
    assert "dict 3" in out
    assert "status=success" in out
    assert "mcp__oa__list_users x1 (ok)" in out


@pytest.mark.asyncio
async def test_call_many_concurrent_and_ordered(bridge) -> None:
    out = await _run(
        'rs = tools.call_many([("mcp__oa__get_expense", {"user_id": u}) for u in ["u1", "u3"]])\n'
        'print([r["amount"] for r in rs])'
    )
    assert "[20000, 500]" in out
    assert "mcp__oa__get_expense x2 (ok)" in out


@pytest.mark.asyncio
async def test_call_many_partial_failure_degrades(bridge) -> None:
    out = await _run(
        'rs = tools.call_many([("mcp__oa__get_expense", {"user_id": "u1"}), ("nope", {})])\n'
        'print("__error__" not in rs[0], "unknown tool" in rs[1]["__error__"])'
    )
    assert "True True" in out
    assert "status=success" in out  # 单项失败不炸整批,代码层处理


@pytest.mark.asyncio
async def test_unknown_tool_error_is_actionable(bridge) -> None:
    out = await _run('tools.call("mcp__oa__list_user")')  # 拼错
    assert "status=error" in out
    assert "unknown tool 'mcp__oa__list_user'" in out
    assert "closest: mcp__oa__list_users" in out  # difflib 近似名
    assert "available" in out


@pytest.mark.asyncio
async def test_contextvar_visible_in_inner_calls(bridge) -> None:
    """内层工具必须读到外层设置的 ContextVar(MCP user_token 透传的前提)。"""
    token = _test_secret.set("secret-token")
    try:
        out = await _run('r = tools.call("mcp__oa__list_users")\nprint(r["_secret_seen"])')
        assert "True" in out
    finally:
        _test_secret.reset(token)


@pytest.mark.asyncio
async def test_example_code_runs_for_real(bridge) -> None:
    """防文档漂移:DESCRIPTION 里的 EXAMPLE_CODE 真实执行并断言行为。"""
    out = await _run(EXAMPLE_CODE)
    # 3 人中 active 2 人(alice/carol),alice 报销 20000 > 10000 触发通知。
    assert '"count": 3' in out
    assert '"flagged": ["alice"]' in out
    assert "mcp__oa__list_users x1 (ok)" in out
    assert "mcp__oa__get_expense x2 (ok)" in out
    assert "mcp__oa__notify_manager x1 (ok)" in out
    assert "status=success" in out


@pytest.mark.asyncio
async def test_multi_tool_dependency_and_condition(bridge) -> None:
    """多工具依赖链 + 条件分支:先查人,按报销金额决定是否通知。"""
    code = (
        'people = tools.call("mcp__oa__list_users")["users"]\n'
        'active = [p for p in people if p.get("status") == "active"]\n'
        'rows = tools.call_many([("mcp__oa__get_expense", {"user_id": p["id"]}) for p in active])\n'
        'big = [p["name"] for p, r in zip(active, rows) if r["amount"] > 1000]\n'
        'if big:\n'
        '    tools.call("mcp__oa__notify_manager", names=big)\n'
        'else:\n'
        '    print("no big spenders")\n'
        'print(len(active), big)'
    )
    out = await _run(code)
    assert "2 ['alice']" in out
    assert "mcp__oa__notify_manager x1 (ok)" in out


# ── 受限环境 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restricted_blocks_os_import(bridge) -> None:
    out = await _run("import os")
    assert "status=error" in out
    assert "not allowed" in out


@pytest.mark.asyncio
async def test_restricted_blocks_open(bridge) -> None:
    out = await _run('open("/etc/passwd")')
    assert "status=error" in out


@pytest.mark.asyncio
async def test_restricted_allows_json(bridge) -> None:
    out = await _run('import json\nprint(json.dumps({"a": 1}))')
    assert '{"a": 1}' in out
    assert "status=success" in out


def test_safe_import_rejects_subprocess() -> None:
    with pytest.raises(ImportError, match="not allowed"):
        _safe_import("subprocess")


# ── 超时与截断 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_timeout_cancels(bridge) -> None:
    async def _slow() -> str:
        await asyncio.sleep(30)
        return "never"

    bridge.tools_map["mcp__oa__slow"] = _make_tool("mcp__oa__slow", _slow)
    bridge.call_timeout = 0.3
    out = await _run('tools.call("mcp__oa__slow")')
    assert "status=error" in out
    assert "timed out after 0.3s" in out


@pytest.mark.asyncio
async def test_overall_timeout(bridge) -> None:
    bridge.overall_timeout = 0.5
    # 有界长循环:超时后线程能在数秒内自然结束,避免无限占用 CPU。
    out = await _run("import time\nfor _ in range(50):\n    time.sleep(0.1)")
    assert "status=error" in out
    assert "timed out" in out


@pytest.mark.asyncio
async def test_stdout_truncation(bridge) -> None:
    bridge.max_output_bytes = 200
    out = await _run('for i in range(1000):\n    print("x" * 100)')
    assert "truncated" in out
    assert len(out) < 5_000


@pytest.mark.asyncio
async def test_syntax_error_returns_traceback(bridge) -> None:
    out = await _run("def broken(:\n    pass")
    assert "status=error" in out
    assert "traceback" in out.lower()


@pytest.mark.asyncio
async def test_no_bridge_context_returns_error() -> None:
    """未注入 ToolBridgeContext 时优雅报错(不抛异常炸 agent)。"""
    # bridge fixture 已 reset,此时再调用即无 context。
    out = await _run('print("hi")')
    assert "Error" in out
    assert "ToolBridgeContext not set" in out


# ── 结果标准化 ─────────────────────────────────────────────────────────


def test_normalize_content_and_artifact_tuple() -> None:
    raw = ([{"type": "text", "text": '{"ok": 1}'}], {"artifact": True})
    assert _normalize_result(raw) == {"ok": 1}


def test_normalize_plain_text() -> None:
    assert _normalize_result("just text") == "just text"


def test_normalize_invalid_json_keeps_text() -> None:
    assert _normalize_result("{broken json") == "{broken json"


# ── 开放模式 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_mode_allows_import(oa_tools: dict[str, StructuredTool]) -> None:
    ctx = ToolBridgeContext(
        tools_map=oa_tools, restricted=False,
        call_timeout=5.0, overall_timeout=10.0,
    )
    token = set_tool_bridge_context(ctx)
    try:
        out = await _run("import os\nprint(os.name)")
        assert "status=success" in out
        assert "posix" in out
    finally:
        reset_tool_bridge_context(token)
