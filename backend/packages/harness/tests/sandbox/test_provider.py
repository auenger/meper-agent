"""AC4 cover: SandboxProvider ABC + 进程级单例。"""
from __future__ import annotations

import pytest

from agent_flow_harness.sandbox.base import Sandbox, SandboxResult
from agent_flow_harness.sandbox.provider import (
    SandboxProvider,
    get_sandbox_provider,
    reset_sandbox_provider,
    set_sandbox_provider,
)


class _FakeSandbox(Sandbox):
    """测试用 Sandbox 桩。"""

    def __init__(self, sid: str = "fake") -> None:
        self._id = sid

    @property
    def id(self) -> str:
        return self._id

    def execute_command(self, command, *, timeout=120):
        return SandboxResult(stdout="ran", stderr="", exit_code=0)

    def read_file(self, path):
        return ""

    def write_file(self, path, content):
        pass

    def edit_file(self, path, old_string, new_string, *, replace_all=False):
        return "edited"

    def write_to_output(self, path, content):
        pass

    def glob(self, path, pattern):
        return []

    def grep(self, path, pattern):
        return []


class _FakeProvider(SandboxProvider):
    def __init__(self):
        self._sandboxes: dict[str, Sandbox] = {}

    def acquire(self, thread_id=None):
        sb = _FakeSandbox(sid=f"sb-{len(self._sandboxes)}")
        self._sandboxes[sb.id] = sb
        return sb

    def get(self, sandbox_id):
        return self._sandboxes.get(sandbox_id)

    def release(self, sandbox_id):
        self._sandboxes.pop(sandbox_id, None)


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        SandboxProvider()  # type: ignore[abstract]


def test_set_get_provider_singleton():
    provider = _FakeProvider()
    set_sandbox_provider(provider)
    try:
        assert get_sandbox_provider() is provider
    finally:
        reset_sandbox_provider()


def test_reset_provider():
    provider = _FakeProvider()
    set_sandbox_provider(provider)
    reset_sandbox_provider()
    new_provider = _FakeProvider()
    set_sandbox_provider(new_provider)
    try:
        assert get_sandbox_provider() is new_provider
        assert get_sandbox_provider() is not provider
    finally:
        reset_sandbox_provider()


def test_provider_acquire_get_release_lifecycle():
    provider = _FakeProvider()
    set_sandbox_provider(provider)
    try:
        sb = provider.acquire("thread-1")
        assert sb.id == "sb-0"
        assert provider.get("sb-0") is sb
        provider.release("sb-0")
        assert provider.get("sb-0") is None
    finally:
        reset_sandbox_provider()
