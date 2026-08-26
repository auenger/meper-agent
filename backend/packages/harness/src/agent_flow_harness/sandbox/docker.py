"""DockerSandbox — Docker 容器沙箱实现（比 LocalSandbox 隔离更强）。

从 backend 的 SandboxExecutor 提取，去掉了 ``app.*`` 依赖，配置通过
DockerSandboxConfig 注入。降级策略 fail-closed：沙箱禁用或 Docker 不可用
时默认**拒绝**执行，仅当 ``allow_local_fallback=True``（显式开发选项）才
降级到本机 subprocess，且每次降级打 ERROR 日志。

安全特性：
- 容器只读根文件系统 + no-new-privileges
- 网络禁用（network_mode="none"）
- 内存/CPU 限制
- tmpfs for /tmp
- 非 root 用户执行

用法：
    config = DockerSandboxConfig(image="agent-sandbox:latest", enabled=True)
    sandbox = DockerSandbox(sandbox_id="s1", work_dir=Path("/workspace"),
                            mounts={"tmp": Path("/workspace/tmp")}, config=config)
    result = sandbox.execute_command("ls -la")
"""
from __future__ import annotations

import contextlib
import os
import re
import subprocess
import time
from pathlib import Path

import structlog

from agent_flow_harness.sandbox.base import GrepMatch, Sandbox, SandboxResult

logger = structlog.get_logger(__name__)


class DockerSandboxConfig:
    """Docker 沙箱配置（从 backend settings 提取，去 app.* 依赖）。

    所有字段有合理默认值，用户按需覆盖。
    """

    def __init__(
        self,
        *,
        image: str = "agent-sandbox:latest",
        enabled: bool = False,
        mem_limit: str = "512m",
        cpu_quota: int = 100_000,
        timeout: int = 120,
        max_output_bytes: int = 1_000_000,
        network_mode: str = "none",
        container_workspace_dir: str = "/workspace",
        container_skills_dir: str = "/skills",
        allow_local_fallback: bool = False,
    ) -> None:
        self.image = image
        self.enabled = enabled
        self.mem_limit = mem_limit
        self.cpu_quota = cpu_quota
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.network_mode = network_mode
        self.container_workspace_dir = container_workspace_dir
        self.container_skills_dir = container_skills_dir
        # Fail-closed 降级闸：False（默认）时沙箱不可用即拒绝执行；
        # True 时降级本机 subprocess（仅限开发环境），每次降级打 ERROR。
        self.allow_local_fallback = allow_local_fallback


class _DockerUnavailableError(Exception):
    """Docker daemon 不可用或镜像缺失。"""


class DockerSandbox(Sandbox):
    """Docker 容器沙箱（强隔离），带显式开启的 subprocess fallback。

    execute_command 优先用 Docker（enabled=True 且 daemon 可达）；沙箱不可
    用时默认拒绝执行，仅 allow_local_fallback=True 时降级 subprocess（与
    LocalSandbox 相同，但每次降级打 ERROR 日志）。
    read_file/write_file/glob/grep 操作宿主机 work_dir（容器挂载的目录）。
    """

    def __init__(
        self,
        sandbox_id: str,
        work_dir: Path,
        *,
        config: DockerSandboxConfig | None = None,
        mounts: dict[str, Path] | None = None,
        timeout: int = 120,
        max_output_chars: int = 50_000,
    ) -> None:
        self._id = sandbox_id
        self._work_dir = Path(work_dir).resolve()
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._config = config or DockerSandboxConfig()
        self._mounts = mounts or {"tmp": self._work_dir / "tmp"}
        self._timeout = timeout
        self._max_output = max_output_chars

    @property
    def id(self) -> str:
        return self._id

    # ── 命令执行 ──────────────────────────────────────────────────────

    def execute_command(self, command: str, *, timeout: int | None = None) -> SandboxResult:
        """执行命令：Docker（enabled）；本机 subprocess 仅在显式允许时降级。"""
        effective_timeout = timeout if timeout is not None else self._timeout

        if not self._config.enabled:
            return self._fallback_or_refuse(
                command, effective_timeout,
                reason="sandbox disabled (enabled=False)",
            )

        try:
            return self._execute_docker(command, effective_timeout)
        except _DockerUnavailableError as exc:
            return self._fallback_or_refuse(
                command, effective_timeout,
                reason=f"docker unavailable ({exc})",
            )
        except Exception as exc:
            return SandboxResult(
                stdout="",
                stderr=f"Sandbox execution error: {exc}",
                exit_code=1,
            )

    def _fallback_or_refuse(
        self, command: str, timeout: int, *, reason: str,
    ) -> SandboxResult:
        """Fail-closed 降级闸。

        默认（allow_local_fallback=False）拒绝执行并返回可操作的错误信息；
        显式开启后降级到本机 subprocess 并打 ERROR 日志——LLM 生成的命令
        正在无隔离地跑在宿主机上，这必须可见、可告警。
        """
        if not self._config.allow_local_fallback:
            return SandboxResult(
                stdout="",
                stderr=(
                    f"Sandbox unavailable and local fallback is not allowed: {reason}. "
                    "Set allow_local_fallback=True (SANDBOX_FALLBACK=local in the app) "
                    "to permit host execution — development only."
                ),
                exit_code=1,
            )
        logger.error(
            "sandbox_local_fallback",
            reason=reason,
            command_preview=command[:200],
            hint="LLM-generated command executing on host without isolation",
        )
        return self._execute_subprocess(command, timeout)

    def _execute_docker(self, command: str, timeout: int) -> SandboxResult:
        """Docker 容器内执行（从 backend SandboxExecutor 提取）。"""
        try:
            import docker  # type: ignore[import-untyped]
        except ImportError as exc:
            raise _DockerUnavailableError("docker package not installed") from exc

        try:
            client = docker.from_env(timeout=30)
            client.ping()
        except Exception as exc:
            raise _DockerUnavailableError(str(exc)) from exc

        # 构建 volume mounts
        ws_dir = self._config.container_workspace_dir
        volumes = {}
        for name, host_path in self._mounts.items():
            mode = "ro" if name == "input" else "rw"
            volumes[str(Path(host_path).resolve())] = {
                "bind": f"{ws_dir}/{name}",
                "mode": mode,
            }

        env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        start = time.monotonic()
        timed_out = False

        try:
            container = client.containers.run(
                image=self._config.image,
                command=["bash", "-c", command],
                volumes=volumes,
                environment=env,
                working_dir=f"{ws_dir}/tmp",
                user="sandbox",
                network_mode=self._config.network_mode,
                read_only=True,
                security_opt=["no-new-privileges"],
                mem_limit=self._config.mem_limit,
                cpu_quota=self._config.cpu_quota,
                cpu_period=100_000,
                tmpfs={"/tmp": "size=64m,noexec"},
                detach=True,
                auto_remove=False,
            )
        except Exception as exc:
            raise _DockerUnavailableError(str(exc)) from exc

        try:
            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", -1)
        except Exception:
            timed_out = True
            exit_code = -1
            with contextlib.suppress(Exception):
                container.kill()

        duration = time.monotonic() - start

        try:
            logs = container.logs(stdout=True, stderr=True)
            stdout_raw = logs if isinstance(logs, bytes) else b""
            stderr_raw = b""
        except Exception:
            stdout_raw = b""
            stderr_raw = b""

        with contextlib.suppress(Exception):
            container.remove(force=True)

        stdout = _truncate(stdout_raw.decode("utf-8", "replace"), self._config.max_output_bytes)
        stderr = _truncate(stderr_raw.decode("utf-8", "replace"), self._config.max_output_bytes)

        if timed_out:
            stderr += f"\n[timeout] Command exceeded {timeout}s and was killed."

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration=duration,
            timed_out=timed_out,
        )

    def _execute_subprocess(self, command: str, timeout: int) -> SandboxResult:
        """subprocess fallback（Docker 不可用时）。"""
        tmp_dir = self._mounts.get("tmp", self._work_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                cwd=str(tmp_dir),
                capture_output=True,
                timeout=timeout,
            )
            duration = time.monotonic() - start
            return SandboxResult(
                stdout=self._truncate(proc.stdout.decode("utf-8", "replace")),
                stderr=self._truncate(proc.stderr.decode("utf-8", "replace")),
                exit_code=proc.returncode,
                duration=duration,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return SandboxResult(
                stdout="",
                stderr=f"[timeout] Command exceeded {timeout}s and was killed.",
                exit_code=-1,
                duration=duration,
                timed_out=True,
            )

    # ── 文件操作（操作宿主机 work_dir，与 LocalSandbox 相同）────────

    def read_file(self, path: str) -> str:
        resolved = self._safe_resolve(path, for_write=False)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")
        content = resolved.read_text(encoding="utf-8", errors="replace")
        return self._truncate(content)

    def write_file(self, path: str, content: str) -> None:
        """写文件到 output 目录（用户可见/可下载）。"""
        resolved = self._safe_resolve_output(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> str:
        """就地编辑 work_dir 内文件（宿主机挂载目录，容器内产物可直接编辑）。"""
        resolved = self._safe_resolve(path, for_write=False)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path}")
        content = resolved.read_text(encoding="utf-8", errors="replace")

        count = content.count(old_string)
        if count == 0:
            raise ValueError(
                f"old_string not found in '{path}'. "
                "Read the file again and retry with an exact match (including whitespace)."
            )
        if count > 1 and not replace_all:
            raise ValueError(
                f"old_string appears {count} times in '{path}'. "
                "Provide a longer, more unique old_string, or set replace_all=true."
            )

        new_content = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )
        resolved.write_text(new_content, encoding="utf-8")
        occurrences = count if replace_all else 1
        return (
            f"Successfully edited '{path}' "
            f"({occurrences} replacement{'s' if occurrences > 1 else ''})."
        )

    def glob(self, path: str, pattern: str) -> list[str]:
        base = self._safe_resolve(path, for_write=False)
        if not base.exists():
            return []
        return sorted(str(p) for p in base.glob(pattern))

    def grep(self, path: str, pattern: str) -> list[GrepMatch]:
        base = self._safe_resolve(path, for_write=False)
        if not base.exists():
            return []
        regex = re.compile(pattern)
        matches: list[GrepMatch] = []
        files = [base] if base.is_file() else list(base.rglob("*"))
        for f in files:
            if not f.is_file():
                continue
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append(GrepMatch(path=str(f), line_number=i, line=line))
        return matches

    # ── 内部 helpers（与 LocalSandbox 相同）──────────────────────────

    def _safe_resolve(self, user_path: str, *, for_write: bool) -> Path:
        """解析路径并校验是否在 work_dir 内（防路径越权）。

        当模型传入绝对路径(如 /tmp/foo/bar.txt)时,提取相对部分映射到
        work_dir 内,避免 PermissionError。
        """
        if os.path.isabs(user_path):
            rel = user_path.lstrip('/')
            for prefix in ('tmp/', 'workspace/tmp/', 'workspace/'):
                if rel.startswith(prefix):
                    rel = rel[len(prefix):]
                    break
            resolved = (self._work_dir / rel).resolve()
        else:
            resolved = (self._work_dir / user_path).resolve()
        try:
            resolved.relative_to(self._work_dir)
        except ValueError as exc:
            msg = f"Access denied — path '{user_path}' outside sandbox work_dir"
            raise PermissionError(msg) from exc
        return resolved

    def _safe_resolve_output(self, user_path: str) -> Path:
        """解析路径并校验是否在 output_dir 内（防路径越权）。

        当模型传入绝对路径时,提取相对部分映射到 output_dir 内。
        """
        output_dir = self._mounts.get("output")
        if output_dir is None:
            output_dir = self._work_dir.parent / "output"
        else:
            output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if os.path.isabs(user_path):
            rel = user_path.lstrip('/')
            for prefix in ('output/', 'workspace/output/', 'workspace/'):
                if rel.startswith(prefix):
                    rel = rel[len(prefix):]
                    break
            resolved = (output_dir / rel).resolve()
        else:
            resolved = (output_dir / user_path).resolve()
        try:
            resolved.relative_to(output_dir)
        except ValueError as exc:
            msg = f"Access denied — path '{user_path}' outside sandbox output_dir"
            raise PermissionError(msg) from exc
        return resolved

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_output:
            return text
        return text[: self._max_output] + f"\n... [truncated: {len(text):,} chars]"


def _truncate(text: str, max_bytes: int) -> str:
    """模块级截断 helper（Docker logs 用）。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", "ignore")
    return truncated + f"\n\n[truncated — output exceeded {max_bytes} bytes]"


__all__ = ["DockerSandbox", "DockerSandboxConfig"]
