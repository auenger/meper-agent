"""资源导入导出 API — afpkg 包的导出下载与上传导入。

统一入口（不设 per-resource 端点）：格式 / 权限 / 报告只有一份实现，
前端各列表页只是组装 ``resources`` 参数。权限：admin / developer。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from app.core.security import get_current_user, require_any_role
from app.schemas.transfer import ExportRequest, ImportReport
from app.schemas.user import UserResponse
from app.services.transfer.exporter import export_package
from app.services.transfer.importer import import_package
from app.utils.http import build_content_disposition

router = APIRouter(
    prefix="/transfer",
    tags=["transfer"],
    dependencies=[Depends(get_current_user)],
)

# 导入 zip 读取上限（与 settings.TRANSFER_MAX_PACKAGE_SIZE 一致的硬防线，
# 防止超大 multipart 在内存里先炸）
_MAX_IMPORT_READ = 200 * 1024 * 1024


@router.post(
    "/export",
    summary="导出资源为 afpkg 包（zip）",
    responses={403: {"description": "Forbidden — developer+ role required"}},
)
async def export_resources(
    body: ExportRequest,
    _: UserResponse = Depends(require_any_role("admin", "developer")),
) -> Response:
    """导出指定资源（可选携带依赖闭包）为 zip 下载。"""
    data = await export_package(
        body.resources,
        include_dependencies=body.include_dependencies,
    )
    filename = f"agentflow-export-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": build_content_disposition(filename)},
    )


@router.post(
    "/import",
    response_model=ImportReport,
    summary="导入 afpkg 包（支持 dry_run 预检）",
    responses={403: {"description": "Forbidden — developer+ role required"}},
)
async def import_resources(
    file: UploadFile = File(..., description="afpkg zip 包"),
    dry_run: bool = Form(False),
    reuse_existing: bool = Form(True),
    conflict: str = Form("rename"),
    auto_discover: bool = Form(True),
    user: UserResponse = Depends(require_any_role("admin", "developer")),
) -> ImportReport:
    """导入一个 afpkg 包；``dry_run=true`` 只做预检返回报告，不落库。"""
    data = await file.read()
    if len(data) > _MAX_IMPORT_READ:
        from app.core.errors import ValidationError

        raise ValidationError(
            code="TRANSFER_PACKAGE_TOO_LARGE",
            message="包体超过 200MB 上限",
        )
    if conflict not in ("rename", "skip"):
        from app.core.errors import ValidationError

        raise ValidationError(
            code="TRANSFER_BAD_CONFLICT",
            message="conflict 仅支持 rename / skip",
        )
    return await import_package(
        data,
        dry_run=dry_run,
        reuse_existing=reuse_existing,
        conflict=conflict,
        auto_discover=auto_discover,
        imported_by=user.id,
    )
