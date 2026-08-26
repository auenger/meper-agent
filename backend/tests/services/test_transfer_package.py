"""Transfer package 层单测 — zip 往返 / zip slip / 上限 / manifest 校验。

纯文件系统测试，不依赖 MongoDB（integration 全家桶之外可独立跑）。
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from app.core.errors import ValidationError
from app.services.transfer import package as pkg


def _zip_of(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_build_and_extract_roundtrip(tmp_path: Path):
    entries = {
        "manifest.json": b"{}",
        "skills/my-skill/SKILL.md": "---\nname: my-skill\n---\nbody",
        "kbs/kb_x/files/docs/readme.md": "# hi",
    }
    data = pkg.build_zip(entries)
    dest = tmp_path / "unpacked"
    dest.mkdir()
    pkg.extract_zip(data, dest)

    assert (dest / "skills/my-skill/SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert (dest / "kbs/kb_x/files/docs/readme.md").read_text(encoding="utf-8") == "# hi"


def test_extract_rejects_zip_slip(tmp_path: Path):
    # 构造一个含 ../ 越界路径的 zip
    _zip_of({"ok.txt": b"fine"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok.txt", "fine")
        zf.writestr("../../etc/evil.txt", "pwned")
    dest = tmp_path / "unpacked"
    dest.mkdir()
    with pytest.raises(ValidationError, match="越界路径"):
        pkg.extract_zip(buf.getvalue(), dest)
    # 越界文件绝不能落盘
    assert not (tmp_path.parent / "etc/evil.txt").exists()
    assert not (dest / "ok.txt").exists()  # 校验全过才写盘 → 一个都不落


def test_extract_rejects_bad_zip(tmp_path: Path):
    dest = tmp_path / "unpacked"
    dest.mkdir()
    with pytest.raises(ValidationError, match="zip"):
        pkg.extract_zip(b"not a zip at all", dest)


def test_extract_rejects_oversized_package(tmp_path: Path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRANSFER_MAX_PACKAGE_SIZE", 10)
    dest = tmp_path / "unpacked"
    dest.mkdir()
    with pytest.raises(ValidationError, match="包体大小"):
        pkg.extract_zip(_zip_of({"a.txt": b"x" * 100}), dest)


def test_extract_rejects_too_many_files(tmp_path: Path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRANSFER_MAX_FILE_COUNT", 2)
    dest = tmp_path / "unpacked"
    dest.mkdir()
    with pytest.raises(ValidationError, match="文件数"):
        pkg.extract_zip(_zip_of({"a.txt": b"1", "b.txt": b"2", "c.txt": b"3"}), dest)


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    d = tmp_path / "pkg"
    d.mkdir(exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_read_manifest_ok(tmp_path: Path):
    d = _write_manifest(
        tmp_path,
        {
            "format": pkg.PACKAGE_FORMAT,
            "format_version": 1,
            "contains_secrets": False,
            "resources": [
                {"kind": "agent", "path": "agents/a.json", "original_id": "a", "name": "A"},
                {"kind": "skill", "name": "my-skill", "original_id": "t1"},
            ],
        },
    )
    manifest = pkg.read_manifest(d)
    assert len(manifest["resources"]) == 2


def test_read_manifest_rejects_missing(tmp_path: Path):
    d = tmp_path / "pkg"
    d.mkdir()
    with pytest.raises(ValidationError, match="manifest"):
        pkg.read_manifest(d)


def test_read_manifest_rejects_wrong_format(tmp_path: Path):
    d = _write_manifest(
        tmp_path,
        {"format": "someone-else", "format_version": 1, "resources": [{"kind": "agent", "name": "A", "path": "p"}]},
    )
    with pytest.raises(ValidationError, match="格式不符"):
        pkg.read_manifest(d)


def test_read_manifest_rejects_future_version(tmp_path: Path):
    d = _write_manifest(
        tmp_path,
        {"format": pkg.PACKAGE_FORMAT, "format_version": pkg.FORMAT_VERSION + 1,
         "resources": [{"kind": "agent", "name": "A", "path": "p"}]},
    )
    with pytest.raises(ValidationError, match="版本"):
        pkg.read_manifest(d)


def test_read_manifest_rejects_secrets_flag(tmp_path: Path):
    d = _write_manifest(
        tmp_path,
        {"format": pkg.PACKAGE_FORMAT, "format_version": 1, "contains_secrets": True,
         "resources": [{"kind": "agent", "name": "A", "path": "p"}]},
    )
    with pytest.raises(ValidationError, match="明文密钥"):
        pkg.read_manifest(d)


def test_read_manifest_rejects_unknown_kind(tmp_path: Path):
    d = _write_manifest(
        tmp_path,
        {"format": pkg.PACKAGE_FORMAT, "format_version": 1,
         "resources": [{"kind": "user_skill", "name": "X", "path": "p"}]},
    )
    with pytest.raises(ValidationError, match="不支持"):
        pkg.read_manifest(d)


# ---------------------------------------------------------------------------
# 回归：_agent_payload 依赖 compat 解析函数（旧 agent_service 私有函数已迁移）
# ---------------------------------------------------------------------------


def test_agent_payload_resolves_compat_fields():
    """_agent_payload 正常导入并使用 compat 的 resolve_*（回归旧 import 断裂）。"""
    from app.services.transfer.exporter import _agent_payload

    doc = {
        "_id": "agent_x",
        "name": "回归 Agent",
        # 扁平字段 + 旧嵌套字段同时存在时取扁平
        "default_model": "model_new",
        "max_retry": 5,
        "llm_config": {"default_model": "model_old", "max_retry": 1},
        "custom_tools": [],
    }
    payload = _agent_payload(doc)
    assert payload["default_model"] == "model_new"
    assert payload["max_retry"] == 5
    assert payload["kind"] == "agent"

    # 纯旧嵌套文档（迁移前形态）走 fallback
    legacy = {
        "_id": "agent_y", "name": "旧形态", "llm_config": {"default_model": "m1", "max_retry": 2},
    }
    p2 = _agent_payload(legacy)
    assert p2["default_model"] == "m1"
    assert p2["max_retry"] == 2
