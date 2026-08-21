"""AC6 cover: SandboxContext + ContextVar set/get/reset。"""
from __future__ import annotations

import pytest

from agent_flow_harness.sandbox.base import Sandbox, SandboxResult
from agent_flow_harness.sandbox.context import (
    SandboxContext,
    get_sandbox_context,
    reset_sandbox_context,
    set_sandbox_context,
)


class _StubSandbox(Sandbox):
    @property
    def id(self):
        return "stub"

    def execute_command(self, command, *, timeout=120):
        return SandboxResult(stdout="", stderr="", exit_code=0)

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


def test_get_without_set_raises():
    try:
        get_sandbox_context()
        pytest.fail("should raise when not set")
    except RuntimeError:
        pass


def test_set_then_get():
    ctx = SandboxContext(sandbox=_StubSandbox())
    token = set_sandbox_context(ctx)
    try:
        assert get_sandbox_context() is ctx
        assert get_sandbox_context().sandbox.id == "stub"
    finally:
        reset_sandbox_context(token)


def test_reset_restores():
    ctx = SandboxContext(sandbox=_StubSandbox())
    token = set_sandbox_context(ctx)
    reset_sandbox_context(token)
    with pytest.raises(RuntimeError):
        get_sandbox_context()
