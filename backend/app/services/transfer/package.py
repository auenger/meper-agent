"""afpkg 包容器 — zip 打包 / 解包 / 安全校验 / manifest 读写。

包格式（.afpkg = zip，见 docs/resource-transfer-plan.md §3）::

    manifest.json                    # 包清单（人类可读）
    agents/{original_id}.json
    workflows/{original_id}.json     # 含非持久化字段 _mcp_tool_refs（MCP 工具重绑依据）
    mcp/{original_id}.json           # auth_config 脱敏
    mcp_categories/{original_id}.json
    models/{original_id}.json        # 不含 api_key
    tools/{original_id}.json         # 自定义工具（openapi/code/prebuilt）
    skills/{name}/...                # 官方 Skill 整目录（SKILL.md + 辅助文件）
    kbs/{original_id}/kb.json
    kbs/{original_id}/files/**.md    # tree（Wiki）KB 的 .md 文件树

安全防护：包体大小 / 解压后总大小 / 文件数上限（防 zip 炸弹），逐条
路径校验（防 zip slip）——校验全量通过后才开始写盘。
"""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

from loguru import logger

from app.core.config import settings
from app.core.errors import ValidationError
from app.schemas.transfer import TRANSFER_KINDS

PACKAGE_FORMAT = "agentflow-package"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"


def build_zip(entries: dict[str, bytes]) -> bytes:
    """把 ``{archive_path: content}`` 打成 zip 字节流。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in entries.items():
            zf.writestr(arcname, content)
    return buf.getvalue()


def extract_zip(data: bytes, dest: Path) -> None:
    """校验并解压 afpkg 到 ``dest``（目录须已存在）。

    Raises:
        ValidationError: 包体超限 / 损坏 / 文件数或解压大小超限 / 含越界路径。
    """
    max_pkg = settings.TRANSFER_MAX_PACKAGE_SIZE
    if len(data) > max_pkg:
        raise ValidationError(
            code="TRANSFER_PACKAGE_TOO_LARGE",
            message=f"包体大小超过上限（{len(data)} > {max_pkg} bytes）",
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValidationError(
            code="TRANSFER_BAD_PACKAGE",
            message="不是有效的 zip 包",
        ) from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > settings.TRANSFER_MAX_FILE_COUNT:
            raise ValidationError(
                code="TRANSFER_PACKAGE_TOO_LARGE",
                message=f"包内文件数超过上限（{len(infos)} > {settings.TRANSFER_MAX_FILE_COUNT}）",
            )
        total_size = sum(i.file_size for i in infos)
        if total_size > settings.TRANSFER_MAX_UNPACKED_SIZE:
            raise ValidationError(
                code="TRANSFER_PACKAGE_TOO_LARGE",
                message=f"解压后总大小超过上限（{total_size} > {settings.TRANSFER_MAX_UNPACKED_SIZE} bytes）",
            )

        dest_resolved = dest.resolve()
        # 两遍式：先全量校验路径（zip slip），任何一个越界即整体拒绝，
        # 不留半解压状态；再统一写盘。
        targets: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in infos:
            # zip slip 防护：归一化后的目标路径必须仍在 dest 内。
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                raise ValidationError(
                    code="TRANSFER_UNSAFE_PATH",
                    message=f"包内含越界路径：{info.filename}",
                )
            targets.append((info, target))

        for info, target in targets:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)

    logger.info("transfer_package_extracted", dest=str(dest), file_count=len(infos))


def write_json_entry(entries: dict[str, bytes], path: str, payload: dict | list) -> None:
    """序列化一个 JSON 资源条目（UTF-8，缩进 2，人类可读）。"""
    entries[path] = (
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    ).encode("utf-8")


def read_json_file(path: Path) -> dict:
    """读取包内一个 JSON 资源文件为 dict（损坏时抛 ValidationError）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(
            code="TRANSFER_BAD_ENTRY",
            message=f"包内资源文件损坏或不是 JSON：{path.name}",
        ) from exc


def build_manifest(resources: list[dict], *, exported_by: str = "") -> dict:
    """构造 manifest.json 内容（created_at 由调用方传入，便于测试）。"""
    from app.models.base import utc_now

    return {
        "format": PACKAGE_FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": utc_now().isoformat(),
        "exported_by": exported_by,
        "contains_secrets": False,
        "resources": resources,
    }


def read_manifest(extract_dir: Path) -> dict:
    """读取并校验 manifest.json。

    Raises:
        ValidationError: manifest 缺失/损坏、format 不符、版本不支持、
            contains_secrets=true（本期不支持含密钥包）。
    """
    path = extract_dir / MANIFEST_NAME
    if not path.is_file():
        raise ValidationError(
            code="TRANSFER_MANIFEST_MISSING",
            message="包内缺少 manifest.json，不是有效的 afpkg 包",
        )
    manifest = read_json_file(path)

    if manifest.get("format") != PACKAGE_FORMAT:
        raise ValidationError(
            code="TRANSFER_FORMAT_MISMATCH",
            message=f"包格式不符（期望 {PACKAGE_FORMAT}，实际 {manifest.get('format')!r}）",
        )
    version = manifest.get("format_version")
    if not isinstance(version, int) or not 1 <= version <= FORMAT_VERSION:
        raise ValidationError(
            code="TRANSFER_VERSION_UNSUPPORTED",
            message=f"包格式版本不支持：{version!r}（当前支持 1..{FORMAT_VERSION}）",
        )
    if manifest.get("contains_secrets"):
        raise ValidationError(
            code="TRANSFER_SECRETS_NOT_SUPPORTED",
            message="该包含有明文密钥，当前版本不支持导入",
        )

    resources = manifest.get("resources")
    if not isinstance(resources, list) or not resources:
        raise ValidationError(
            code="TRANSFER_MANIFEST_INVALID",
            message="manifest.resources 为空或格式不正确",
        )
    for res in resources:
        if not isinstance(res, dict):
            raise ValidationError(
                code="TRANSFER_MANIFEST_INVALID",
                message="manifest.resources 含非对象条目",
            )
        kind = res.get("kind")
        if kind not in TRANSFER_KINDS:
            raise ValidationError(
                code="TRANSFER_KIND_UNSUPPORTED",
                message=f"不支持的资源类型：{kind!r}",
            )
        if not res.get("name"):
            raise ValidationError(
                code="TRANSFER_MANIFEST_INVALID",
                message="manifest.resources 条目缺少 name",
            )
        if kind != "skill" and not res.get("path"):
            raise ValidationError(
                code="TRANSFER_MANIFEST_INVALID",
                message="manifest.resources 条目缺少 path",
            )

    return manifest
