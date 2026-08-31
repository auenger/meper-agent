"""view_image 工具测试 — 权限校验、成功返回多模态块、错误分支。"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from app.engine.agent.builtin_tools import (
    reset_workspace_context,
    set_workspace_context,
)
from app.engine.agent.image_tool import view_image
from app.engine.tool.workspace import Workspace
from app.models.file_library import FileConsumerKind, FileRef


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "user_1" / "sess_1"
    ws = Workspace(
        root=root,
        input_dir=root / "input",
        output_dir=root / "output",
        tmp_dir=root / "tmp",
    )
    for d in (ws.input_dir, ws.output_dir, ws.tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
    token = set_workspace_context(ws)
    yield ws
    reset_workspace_context(token)


def _png_bytes() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (32, 32), color=(0, 128, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _fref(
    fid: str = "file_01TEST", *, owner: str = "user_1", origin: str = "sess_1",
    mime: str = "image/png", name: str = "shot.png",
) -> FileRef:
    return FileRef(
        id=fid, owner_user_id=owner, storage_key=f"{owner}/files/{fid}",
        name=name, size=2048, sha256="0" * 64,
        origin_kind=FileConsumerKind.SESSION_MESSAGE, origin_id=origin,
        mime_type=mime,
    )


def _patch_file_service(monkeypatch, fref: FileRef | None, data: bytes) -> None:
    from app.services.file_service import FileService

    async def fake_load_content(self, file_id: str):
        return None if fref is None else (fref, data)

    monkeypatch.setattr(FileService, "load_content", fake_load_content)


async def test_view_image_success(workspace, monkeypatch) -> None:
    _patch_file_service(monkeypatch, _fref(), _png_bytes())

    result = await view_image.ainvoke({"file_id": "file_01TEST"})
    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert result[0]["text"] == '[IMAGE file_id="file_01TEST" name="shot.png"]'
    assert result[1]["type"] == "image_url"
    assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_view_image_owner_mismatch(workspace, monkeypatch) -> None:
    _patch_file_service(monkeypatch, _fref(owner="user_2"), _png_bytes())

    result = await view_image.ainvoke({"file_id": "file_01TEST"})
    assert isinstance(result, str)
    assert result.startswith("[view_image]")
    assert "不属于当前用户" in result


async def test_view_image_other_session_rejected(workspace, monkeypatch) -> None:
    """别的会话的附件 → 拒绝(防跨会话任意文件读取)。"""
    _patch_file_service(monkeypatch, _fref(origin="sess_OTHER"), _png_bytes())

    result = await view_image.ainvoke({"file_id": "file_01TEST"})
    assert isinstance(result, str)
    assert "不是当前会话" in result


async def test_view_image_not_found(workspace, monkeypatch) -> None:
    _patch_file_service(monkeypatch, None, b"")

    result = await view_image.ainvoke({"file_id": "file_missing"})
    assert isinstance(result, str)
    assert "不存在" in result


async def test_view_image_non_image_rejected(workspace, monkeypatch) -> None:
    _patch_file_service(monkeypatch, _fref(mime="text/plain", name="a.txt"), b"hi")

    result = await view_image.ainvoke({"file_id": "file_01TEST"})
    assert isinstance(result, str)
    assert "不是图片" in result
