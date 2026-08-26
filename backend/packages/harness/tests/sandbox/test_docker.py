"""DockerSandbox 测试 — 降级闸（fail-closed）与 subprocess 路径（Docker 在 CI 不可用）。"""
from __future__ import annotations


import pytest

from agent_flow_harness.sandbox.docker import DockerSandbox, DockerSandboxConfig


def _make_sandbox(tmp_path, enabled=False, allow_local_fallback=True) -> DockerSandbox:
    """显式开启降级以测试 subprocess 路径；默认配置的拒绝路径另有单测覆盖。"""
    config = DockerSandboxConfig(enabled=enabled, allow_local_fallback=allow_local_fallback)
    return DockerSandbox(
        sandbox_id="test",
        work_dir=tmp_path,
        mounts={"tmp": tmp_path / "tmp", "output": tmp_path / "output"},
        config=config,
        timeout=10,
    )


def test_subprocess_fallback_success(tmp_path):
    """enabled=False + 显式允许降级 → subprocess 执行。"""
    sb = _make_sandbox(tmp_path)
    result = sb.execute_command("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.stdout


def test_subprocess_fallback_nonzero(tmp_path):
    sb = _make_sandbox(tmp_path)
    result = sb.execute_command("exit 3")
    assert result.exit_code == 3


def test_subprocess_fallback_timeout(tmp_path):
    sb = _make_sandbox(tmp_path, enabled=False)
    sb._timeout = 1
    result = sb.execute_command("sleep 10")
    assert result.timed_out is True


def test_disabled_without_fallback_refuses(tmp_path):
    """enabled=False 且未允许降级（默认）→ fail-closed 拒绝执行。"""
    sb = _make_sandbox(tmp_path, enabled=False, allow_local_fallback=False)
    result = sb.execute_command("echo should_not_run")
    assert result.exit_code == 1
    assert "fallback is not allowed" in result.stderr


def test_docker_fallback_on_unavailable(tmp_path):
    """enabled=True 但 Docker 不可用 + 显式允许降级 → fallback 到 subprocess。"""
    config = DockerSandboxConfig(
        enabled=True, image="nonexistent:latest", allow_local_fallback=True,
    )
    sb = DockerSandbox(
        sandbox_id="t", work_dir=tmp_path, mounts={"tmp": tmp_path},
        config=config, timeout=10,
    )
    result = sb.execute_command("echo fallback")
    # Docker 不可用 → fallback subprocess
    assert result.exit_code == 0
    assert "fallback" in result.stdout


def test_docker_unavailable_without_fallback_refuses(tmp_path):
    """enabled=True 但 Docker 不可用且未允许降级 → fail-closed 拒绝执行。"""
    config = DockerSandboxConfig(enabled=True, image="nonexistent:latest")
    sb = DockerSandbox(
        sandbox_id="t", work_dir=tmp_path, mounts={"tmp": tmp_path},
        config=config, timeout=10,
    )
    result = sb.execute_command("echo should_not_run")
    assert result.exit_code == 1
    assert "docker unavailable" in result.stderr


def test_docker_config_defaults():
    config = DockerSandboxConfig()
    assert config.image == "agent-sandbox:latest"
    assert config.enabled is False
    assert config.mem_limit == "512m"
    assert config.network_mode == "none"
    assert config.allow_local_fallback is False


def test_docker_config_custom():
    config = DockerSandboxConfig(
        image="my-sandbox:v2",
        enabled=True,
        mem_limit="2g",
        cpu_quota=200_000,
        network_mode="bridge",
    )
    assert config.image == "my-sandbox:v2"
    assert config.enabled is True
    assert config.mem_limit == "2g"
    assert config.network_mode == "bridge"


def test_id_property(tmp_path):
    sb = DockerSandbox(sandbox_id="my-id", work_dir=tmp_path)
    assert sb.id == "my-id"


def test_file_operations_work(tmp_path):
    """文件操作: write 写到 output_dir, read/glob/grep 读 work_dir。"""
    sb = _make_sandbox(tmp_path)
    # write goes to output_dir
    sb.write_file("note.txt", "hello world")
    assert (tmp_path / "output" / "note.txt").read_text() == "hello world"

    # read/glob operate on work_dir
    (tmp_path / "a.py").write_text("print('x')")
    matches = sb.glob(".", "*.py")
    assert any("a.py" in m for m in matches)


def test_path_traversal_blocked(tmp_path):
    sb = _make_sandbox(tmp_path)
    with pytest.raises((PermissionError, ValueError)):
        sb.read_file("../../../etc/passwd")
