"""Sandbox 环境抽象 — 执行环境的统一接口（v0.2-2 三层工具模型第二层）。

工具代码（bash/read/write/glob/grep）只调 Sandbox 的抽象方法，从不直接做 I/O。
具体实现（Local/Docker/E2B）通过 SandboxProvider 注入，换实现不改工具。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SandboxResult:
    """命令执行结果。"""

    stdout: str
    stderr: str
    exit_code: int
    duration: float = 0.0
    timed_out: bool = False


@dataclass
class GrepMatch:
    """grep 单条匹配结果。"""

    path: str
    line_number: int
    line: str


class Sandbox(ABC):
    """执行环境抽象。工具代码只调这些方法，从不直接做 I/O。"""

    @property
    @abstractmethod
    def id(self) -> str:
        """sandbox 唯一标识。"""
        ...

    @abstractmethod
    def execute_command(self, command: str, *, timeout: int = 120) -> SandboxResult:
        """执行 shell 命令。"""
        ...

    @abstractmethod
    def read_file(self, path: str) -> str:
        """读文件内容。"""
        ...

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """写文件到 output 目录（用户可见/可下载）。"""
        ...

    @abstractmethod
    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> str:
        """就地编辑 work_dir 内文件（字符串替换）。

        old_string 在文件中必须唯一（replace_all=False 时），否则抛
        ValueError 说明匹配次数；未匹配抛 ValueError。返回成功消息。
        """
        ...

    @abstractmethod
    def glob(self, path: str, pattern: str) -> list[str]:
        """glob 文件匹配。"""
        ...

    @abstractmethod
    def grep(self, path: str, pattern: str) -> list[GrepMatch]:
        """grep 内容搜索。"""
        ...


__all__ = ["Sandbox", "SandboxResult", "GrepMatch"]
