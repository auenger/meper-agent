"""run_code — 代码即工具编排（code-as-orchestration）。

让 LLM 写一段 Python 代码，在代码里通过 ``tools.call / call_many``
直接调用当前 Agent 绑定的其他工具（MCP/自定义/workflow 等），
把「N 轮 LLM-tool 循环」压缩为一次工具调用。覆盖两类场景：

① 批量循环：查所有人 → 逐人查报销（N+1 次调用 → 1 次 run_code）；
② 多工具编排：A 的结果作为 B 的参数、条件分支、并行 join、扇出聚合。

执行模型（同步 exec ↔ 异步工具的桥接）::

    事件循环线程                          exec 工作线程 (daemon)
    ─────────────                        ─────────────────────
    run_code 被 ToolNode 调用
      ctx = copy_context()  ← 快照全部 ContextVar（含 MCP user_token）
      Thread(ctx.run, exec) ──────────→  exec(code) 开始
      await 线程结束 Event                 tools.call(...) 同步阻塞
        ← run_coroutine_threadsafe ─────    (future.result 等待)
      tool.ainvoke(args) 在 loop 执行
      set_result ─────────────────────→  拿到结果继续跑
    读取 stdout buffer → 组装返回文本

关键设计：
- 手动 daemon 线程 + ``ctx.run()``（不用 run_in_executor：池线程不继承
  ContextVar，会丢 MCP 凭证透传；``call_soon_threadsafe`` 在调用线程
  copy context → Task 复制到正确 context → 内层工具读到 user_token）。
- 不替换全局 ``sys.stdout``（进程级、会污染并发 Agent），注入自定义
  ``print`` 写本次执行私有的 buffer。
- 单次调用超时 ``fut.cancel()`` 会级联取消事件循环上的 Task；
  整体超时后 daemon 线程自然结束（泄漏可接受）。
- 受限模式：builtins 白名单 + import 模块白名单（不是强沙箱，但风险
  低于 bash 工具；宿主可用 ``ToolBridgeContext.restricted=False`` 放开）。

四层「让 AI 会写代码的标准」：
① Schema 层：``{code: str}``；
② 协议文档层：``DESCRIPTION``（环境规格 + tools API 契约 + 编排模式）；
③ 策略层：宿主 system prompt 的硬性规则（builder.py 负责）；
④ 自愈层：可操作的错误消息（unknown tool 带 available + difflib
  近似名、参数错透传校验错误、代码错带 traceback）。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import difflib
import json
import threading
import time
import traceback
from types import SimpleNamespace
from typing import Any

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agent_flow_harness.tools.tool_bridge_context import (
    ToolBridgeContext,
    format_call_stats,
    get_tool_bridge_context,
    record_call_stat,
)

logger = structlog.get_logger(__name__)


# ── ① Schema 层 ────────────────────────────────────────────────────────


class RunCodeInput(BaseModel):
    """run_code 参数：一段在受限环境中执行的 Python 代码。"""

    code: str = Field(
        ...,
        description=(
            "Python code to orchestrate tool calls. Use tools.call / "
            "tools.call_many to invoke other tools, print() the final result."
        ),
    )


# ── ② 协议文档层（AI 可读的代码环境规格书）────────────────────────────


DESCRIPTION = """\
Execute Python code with programmatic access to this agent's tools — \
code-as-orchestration. Use this whenever calling a tool repeatedly \
(per-item lookups, batches) OR chaining multiple tools where later calls \
depend on earlier results, instead of issuing one tool call per item.

Environment:
- Python 3.12. print() output is captured and returned to you.
- Stdlib whitelist: json, math, re, datetime, collections, itertools, \
statistics, functools, random, decimal, textwrap, uuid, hashlib, base64, \
csv, io.StringIO, time. No file/network/os/subprocess access.

Tool API (global object `tools`):
- tools.call(name, **kwargs) -> Any
    Call ONE tool synchronously. `name` and `kwargs` are IDENTICAL to \
calling that tool directly (e.g. tools.call("mcp__oa__get_user", \
user_id="u1")). Result is JSON-parsed to dict/list when possible, \
otherwise the raw string. Raises RuntimeError on failure — wrap in \
try/except to skip bad items.
- tools.call_many([(name, kwargs), ...]) -> list[Any]
    Execute calls CONCURRENTLY. Results return in order; a failed item \
becomes {"__error__": "<reason>"} instead of raising. Use for batches.
- tools.list() -> dict
    Runtime discovery: callable tool names -> param type summary.

Orchestration patterns:
- Sequential dependency: r1 = tools.call(A, ...); r2 = tools.call(B, id=r1["id"])
- Fan-out:            tools.call_many([(A, {...}) for x in items])
- Parallel join:      a, b, c = tools.call_many([(A, {}), (B, {}), (C, {})])
- Conditional:        if r.get("ok"): tools.call(B, ...) else: tools.call(C, ...)
- Mixed:              fan-out -> aggregate in code -> dependent follow-up calls

Best practices:
1. Probe first: call once on ONE item and print the result to learn its \
shape, then batch the rest.
2. Use call_many for loops over lists — concurrent, much faster.
3. print() only the FINAL compact result (it re-enters your context); \
keep intermediate data in variables.

Example:
import json
res = tools.call("mcp__oa__list_users", department="sales")
people = res["users"]
active = [p for p in people if p.get("status") == "active"]
rows = tools.call_many([
    ("mcp__oa__get_expense", {"user_id": p["id"]}) for p in active
])
flagged = [p["name"] for p, r in zip(active, rows)
           if isinstance(r, dict) and r.get("amount", 0) > 10000]
if flagged:
    tools.call("mcp__oa__notify_manager", names=flagged, topic="expense review")
print(json.dumps({"count": len(people), "flagged": flagged}, ensure_ascii=False))
"""

# 防文档漂移：单测会真实执行这段代码（配 FakeTool）断言行为。
EXAMPLE_CODE = '''\
import json
res = tools.call("mcp__oa__list_users", department="sales")
people = res["users"]
active = [p for p in people if p.get("status") == "active"]
rows = tools.call_many([
    ("mcp__oa__get_expense", {"user_id": p["id"]}) for p in active
])
flagged = [p["name"] for p, r in zip(active, rows)
           if isinstance(r, dict) and r.get("amount", 0) > 10000]
if flagged:
    tools.call("mcp__oa__notify_manager", names=flagged, topic="expense review")
print(json.dumps({"count": len(people), "flagged": flagged}, ensure_ascii=False))
'''


# ── 受限执行环境 ───────────────────────────────────────────────────────

_SAFE_MODULES: frozenset[str] = frozenset({
    "json", "math", "re", "datetime", "collections", "itertools",
    "statistics", "functools", "random", "decimal", "textwrap", "uuid",
    "hashlib", "base64", "csv", "io", "string", "difflib", "time",
})


def _safe_import(name: str, *_args: Any, **_kwargs: Any) -> Any:
    """受限模式的 ``__import__``：只放行白名单顶层模块。"""
    import importlib

    root = name.split(".")[0]
    if root not in _SAFE_MODULES:
        msg = (
            f"module '{name}' is not allowed in run_code restricted mode; "
            f"allowed: {sorted(_SAFE_MODULES)}"
        )
        raise ImportError(msg)
    return importlib.import_module(name)


_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs, "all": all, "any": any, "bool": bool, "bytes": bytes,
    "chr": chr, "dict": dict, "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float, "format": format, "frozenset": frozenset,
    "getattr": getattr, "hasattr": hasattr, "hash": hash, "int": int,
    "isinstance": isinstance, "issubclass": issubclass, "iter": iter,
    "len": len, "list": list, "map": map, "max": max, "min": min,
    "next": next, "object": object, "pow": pow, "range": range, "repr": repr,
    "reversed": reversed, "round": round, "set": set, "slice": slice,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "type": type,
    "zip": zip, "__import__": _safe_import, "True": True, "False": False,
    "None": None,
    # 异常族：让代码能 try/except 与 raise
    "BaseException": BaseException, "Exception": Exception,
    "ArithmeticError": ArithmeticError, "AssertionError": AssertionError,
    "AttributeError": AttributeError, "IndexError": IndexError,
    "KeyError": KeyError, "LookupError": LookupError,
    "NotImplementedError": NotImplementedError, "RuntimeError": RuntimeError,
    "StopIteration": StopIteration, "TimeoutError": TimeoutError,
    "TypeError": TypeError, "UnicodeDecodeError": UnicodeDecodeError,
    "UnicodeEncodeError": UnicodeEncodeError, "ValueError": ValueError,
    "ZeroDivisionError": ZeroDivisionError, "ImportError": ImportError,
    "IsADirectoryError": IsADirectoryError, "FileNotFoundError": FileNotFoundError,
}

# 开放模式的 builtins = 完整 builtins 但 print 仍替换为线程私有实现。


def _make_open_builtins(print_fn: Any) -> dict[str, Any]:
    import builtins as _builtins

    out: dict[str, Any] = dict(vars(_builtins))
    out["print"] = print_fn
    return out


# ── 线程私有 stdout buffer ─────────────────────────────────────────────


class _StdoutBuffer:
    """本次 run_code 执行私有的输出缓冲（线程内安全，超限截断标记）。

    不替换全局 sys.stdout —— 那是进程级的，会污染并发执行的其他 Agent。
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def write(self, text: str) -> None:
        if self.truncated:
            return
        encoded_len = len(text.encode("utf-8", errors="replace"))
        if self._size + encoded_len > self.max_bytes:
            remaining = self.max_bytes - self._size
            if remaining > 0:
                # 按字节占比保守估字符数，宁少勿超。
                ratio = remaining / encoded_len
                self._parts.append(text[: int(len(text) * ratio)])
            self.truncated = True
            return
        self._parts.append(text)
        self._size += encoded_len

    def getvalue(self) -> str:
        out = "".join(self._parts)
        if self.truncated:
            out += f"\n... (output truncated at {self.max_bytes} bytes)"
        return out


def _make_print(buf: _StdoutBuffer) -> Any:
    """构造写入私有 buffer 的 print（签名兼容内建 print 的常用形态）。"""

    def _print(*args: Any, sep: str = " ", end: str = "\n",
               file: Any = None, flush: bool = False) -> None:
        buf.write(sep.join(str(a) for a in args) + end)

    return _print


# ── 结果标准化 ─────────────────────────────────────────────────────────


def _extract_content_text(raw: Any) -> str:
    """提取工具结果的文本（兼容 MCP content_and_artifact 格式）。

    langchain-mcp-adapters 的工具 ``response_format="content_and_artifact"``，
    ``ainvoke`` 返回 ``(content_list, artifact)`` 元组；content_list 是
    ``[{"type": "text", "text": ...}, ...]`` 块列表。与旧引擎
    ``react.py::_extract_content_artifact_text`` 同一逻辑。
    """
    content: Any = raw
    if isinstance(raw, tuple) and len(raw) == 2:
        content = raw[0]
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return content if isinstance(content, str) else str(content)


def _normalize_result(raw: Any) -> Any:
    """工具结果 → 代码友好形态：JSON 优先解析为 dict/list，否则原文本。"""
    text = _extract_content_text(raw)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    return text


# ── ④ 自愈层：可操作的错误消息 ────────────────────────────────────────


def _unknown_tool_message(name: str, bridge: ToolBridgeContext) -> str:
    available = sorted(bridge.tools_map)
    closest = difflib.get_close_matches(name, available, n=1, cutoff=0.5)
    hint = f"; closest: {closest[0]}" if closest else ""
    return f"unknown tool '{name}'; available: {available}{hint}"


# ── 事件循环侧：真正的工具调用 ─────────────────────────────────────────


async def _invoke_tool(bridge: ToolBridgeContext, name: str, kwargs: dict[str, Any]) -> Any:
    """在事件循环上执行一次内层工具调用（含统计与错误包装）。"""
    tool = bridge.tools_map.get(name)
    if tool is None:
        raise RuntimeError(_unknown_tool_message(name, bridge))
    try:
        raw = await tool.ainvoke(kwargs)
    except Exception as exc:
        record_call_stat(bridge, name, ok=False)
        raise RuntimeError(f"tool '{name}' failed: {exc}") from exc
    record_call_stat(bridge, name, ok=True)
    return _normalize_result(raw)


async def _invoke_batch(bridge: ToolBridgeContext, calls: list[Any]) -> list[Any]:
    """并发执行一批调用：单个失败降级为 ``{"__error__": ...}`` 不炸整批。"""

    async def _one(item: Any) -> Any:
        name, kwargs = item
        try:
            return await _invoke_tool(bridge, name, dict(kwargs))
        except Exception as exc:  # noqa: BLE001 — 单项失败语义即如此
            return {"__error__": str(exc)}

    return list(await asyncio.gather(*(_one(item) for item in calls)))


# ── 工作线程侧：同步外观桥接 ───────────────────────────────────────────


def _make_tools_namespace(
    bridge: ToolBridgeContext, loop: asyncio.AbstractEventLoop
) -> SimpleNamespace:
    """构造注入代码全局的 ``tools`` 对象（call/call_many/list）。

    call / call_many 在工作线程被同步调用，通过
    ``run_coroutine_threadsafe`` 把协程提交回事件循环执行。
    """

    def call(name: str, **kwargs: Any) -> Any:
        fut = asyncio.run_coroutine_threadsafe(
            _invoke_tool(bridge, name, kwargs), loop
        )
        try:
            return fut.result(timeout=bridge.call_timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()  # 级联取消事件循环上的 Task
            msg = f"tool '{name}' timed out after {bridge.call_timeout}s"
            raise RuntimeError(msg) from None

    def call_many(calls: list[Any]) -> list[Any]:
        fut = asyncio.run_coroutine_threadsafe(
            _invoke_batch(bridge, list(calls)), loop
        )
        # 批量超时给足：单次上限 × 批大小的保守近似，封顶 10 分钟。
        timeout = min(bridge.call_timeout * max(1, len(calls)), 600.0)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            msg = f"call_many({len(calls)} calls) timed out after {timeout}s"
            raise RuntimeError(msg) from None

    def list_tools() -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for name, t in bridge.tools_map.items():
            props: dict[str, str] = {}
            schema = getattr(t, "args_schema", None)
            if schema is not None:
                try:
                    raw = schema.model_json_schema().get("properties", {})
                    props = {k: str(v.get("type", "?")) for k, v in raw.items()}
                except Exception:  # noqa: BLE001 — 摘要失败不影响执行
                    pass
            out[name] = props
        return out

    return SimpleNamespace(call=call, call_many=call_many, list=list_tools)


# ── 执行引擎 ───────────────────────────────────────────────────────────


async def _run_code(code: str) -> str:
    """run_code 工具主体：受限/开放 exec + 工具桥接 + 结果组装。"""
    try:
        bridge = get_tool_bridge_context()
    except RuntimeError as exc:
        return f"Error: {exc}"

    loop = asyncio.get_running_loop()
    buf = _StdoutBuffer(bridge.max_output_bytes)
    print_fn = _make_print(buf)

    tools_ns = _make_tools_namespace(bridge, loop)

    if bridge.restricted:
        builtins_dict: dict[str, Any] = dict(_SAFE_BUILTINS)
        builtins_dict["print"] = print_fn
    else:
        builtins_dict = _make_open_builtins(print_fn)

    g: dict[str, Any] = {
        "__name__": "__run_code__",
        "__builtins__": builtins_dict,
        "tools": tools_ns,
        "print": print_fn,
    }

    exec_error: list[str] = []
    done = threading.Event()

    def _worker() -> None:
        try:
            exec(compile(code, "<run_code>", "exec"), g, g)  # noqa: S102
        except BaseException:  # noqa: BLE001 — 全部捕获转 traceback 文本
            exec_error.append(traceback.format_exc(limit=20))
        finally:
            done.set()

    # 快照当前 context（含 MCP user_token 等 ContextVar）并在工作线程
    # 恢复 —— 保证 run_coroutine_threadsafe 创建的 Task 也能读到它们。
    ctx = contextvars.copy_context()
    worker = threading.Thread(
        target=ctx.run, args=(_worker,), daemon=True, name="run_code-exec"
    )
    worker.start()

    async def _wait_done() -> None:
        # Event.wait 放到默认线程池里等，不阻塞事件循环。
        await loop.run_in_executor(None, done.wait)

    started = time.monotonic()
    timed_out = False
    try:
        await asyncio.wait_for(_wait_done(), timeout=bridge.overall_timeout)
    except asyncio.TimeoutError:
        timed_out = True
    duration = time.monotonic() - started

    stats = format_call_stats(bridge)
    header = f"[run_code] status={'error' if (timed_out or exec_error) else 'success'} duration={duration:.1f}s"
    if stats:
        header += f"\ntool calls: {stats}"

    sections: list[str] = [header]

    if timed_out:
        sections.append(
            f"Error: code execution timed out after {bridge.overall_timeout}s "
            "(the worker thread was abandoned; reduce the batch size or "
            "optimize the code, then retry)."
        )
    elif exec_error:
        sections.append("----- traceback -----")
        sections.append(exec_error[0].rstrip())

    stdout = buf.getvalue()
    sections.append(
        f"----- {'partial ' if (timed_out or exec_error) else ''}stdout -----"
    )
    sections.append(stdout if stdout else "(no output)")
    sections.append("----- end -----")

    if timed_out or exec_error:
        logger.warning(
            "run_code_failed",
            duration=round(duration, 1),
            timed_out=timed_out,
            error=bool(exec_error),
            stats=bridge.stats,
        )
    else:
        logger.info("run_code_ok", duration=round(duration, 1), stats=bridge.stats)

    return "\n".join(sections)


run_code = StructuredTool.from_function(
    _run_code, name="run_code", description=DESCRIPTION,
    args_schema=RunCodeInput, coroutine=_run_code,
)


__all__ = ["run_code", "RunCodeInput", "DESCRIPTION", "EXAMPLE_CODE"]
