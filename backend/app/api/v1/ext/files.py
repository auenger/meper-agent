"""External API — session file upload/download (API Key authenticated).

Mirrors ``/v1/sessions/*/files`` but authenticates via API Key. Ownership
is resolved through :func:`resolve_user_id` (终端用户身份来自通用 token
记录 id), matching how ``/v1/ext/agents/*/invoke`` attributes sessions.

File-handling helpers (size limit, extension whitelist, filename
sanitization, path-traversal defense) are imported verbatim from the
internal sessions module to keep both surfaces in lockstep.
"""
import io
import mimetypes
import zipfile
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.api.v1.ext import auth_and_rate_limit, resolve_user_id
from app.api.v1.sessions import (
    ALLOWED_UPLOAD_EXTENSIONS,
    MAX_FILE_SIZE,
    _get_file_service,
    _msg_to_response,
    _resolve_input_filename,
    _sanitize_upload_filename,
)
from app.core.auth_apikey import ApiKeyPrincipal
from app.core.errors import NotFoundError
from app.engine.tool.workspace import Workspace, WorkspaceManager
from app.models.file_library import FileConsumerKind
from app.schemas.file_library import FileRefResponse
from app.schemas.session import ChatFileUploadResponse
from app.services.file_service import FileService
from app.services.session_service import MessageService, SessionService
from app.utils.http import build_content_disposition
from app.utils.sanitize import sanitize_text

router = APIRouter(tags=["external-session-files"])


async def _verify_ext_session_ownership(
    session_id: str,
    principal: ApiKeyPrincipal,
) -> Workspace:
    """Verify the session belongs to the resolved end-user; return workspace.

    ``user_id`` comes from :func:`resolve_user_id` (mcp_token_credentials._id),
    matching how ``/v1/ext/agents/*/invoke`` attributes sessions.
    """
    user_id = resolve_user_id(principal)
    session_doc = await SessionService.get_session(session_id)
    if session_doc is None or session_doc.get("user_id") != user_id:
        raise NotFoundError(code="SESSION_NOT_FOUND", message="会话不存在")
    return WorkspaceManager.get_workspace(user_id, session_id)


@router.post(
    "/sessions/{session_id}/files/upload",
    status_code=201,
    response_model=ChatFileUploadResponse,
    summary="Upload a file to a chat session (external)",
    responses={413: {"description": "File too large"}},
)
async def upload_chat_file(
    session_id: str,
    file: UploadFile = File(...),
    content: str = Form(""),
    svc: FileService = Depends(_get_file_service),
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> ChatFileUploadResponse:
    """Upload a file to a chat session via the external API.

    Creates a FileRef owned by the resolved end-user, copies the file to
    the workspace ``input/`` dir, and optionally attaches it to a user
    message when ``content`` is provided.
    """
    principal.require_scope("agents:invoke")

    ws = await _verify_ext_session_ownership(session_id, principal)
    user_id = resolve_user_id(principal)

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小超过限制（最大 {MAX_FILE_SIZE // 1024 // 1024}MB）",
        )

    mime_type = file.content_type or "application/octet-stream"
    filename = _sanitize_upload_filename(file.filename or "unnamed")

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"不支持的文件类型 {ext or '(无扩展名)'}。"
                "仅允许图片/文档/常见代码与文本文件。"
            ),
        )

    # SVG 是常见的 XSS 载荷载体（内嵌 <script>/onload），入库前定向清洗。
    if ext == ".svg":
        with suppress(Exception):
            data = sanitize_text(data.decode("utf-8", errors="replace")).encode(
                "utf-8"
            )

    file_ref = await svc.create(
        data=data,
        filename=filename,
        mime_type=mime_type,
        owner_user_id=user_id,
        origin_kind=FileConsumerKind.SESSION_MESSAGE,
        origin_id=session_id,
    )

    await svc.add_usage(
        file_id=file_ref.id,
        consumer_kind=FileConsumerKind.SESSION_MESSAGE,
        consumer_id=session_id,
    )

    input_path = _resolve_input_filename(ws.input_dir, filename)
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(data)

    message_response = None
    if content:
        msg_doc = await MessageService.add_message(
            session_id=session_id,
            role="user",
            content=content,
            file_ids=[file_ref.id],
        )
        message_response = _msg_to_response(msg_doc)

    return ChatFileUploadResponse(
        file=FileRefResponse(**file_ref.model_dump(by_alias=True)),
        message=message_response,
        workspace_path=str(input_path.relative_to(ws.input_dir)),
    )


@router.get(
    "/files/{file_id}/download",
    summary="Download an uploaded file by id (external)",
    responses={404: {"description": "File not found"}},
)
async def download_file(
    file_id: str,
    svc: FileService = Depends(_get_file_service),
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> Response:
    """按 file_id 下载已上传的文件（external）。

    所有权校验与内部 ``/files/{file_id}/download`` 一致：owner 为解析出的
    终端用户，或 ``"agent"``（chat agent 产出的公共文件，任何已认证用户
    可访问）。补齐 ext 模式"点击已上传附件下载"的链路——此前 ext 未暴露
    按 id 下载，前端只能报下载失败。
    """
    principal.require_scope("agents:invoke")
    user_id = resolve_user_id(principal)

    file_ref = await svc.get(file_id)
    if file_ref is None or (
        file_ref.owner_user_id != "agent" and file_ref.owner_user_id != user_id
    ):
        raise NotFoundError(
            code="FILE_NOT_FOUND", message=f"文件 {file_id} 不存在"
        )

    try:
        data = await svc._storage.load(file_ref.storage_key)
    except FileNotFoundError:
        raise NotFoundError(
            code="FILE_CONTENT_NOT_FOUND", message="文件内容不存在"
        ) from None

    return Response(
        content=data,
        media_type=file_ref.mime_type,
        headers={"Content-Disposition": build_content_disposition(file_ref.name)},
    )


@router.get(
    "/sessions/{session_id}/files",
    summary="List output files for a session (external)",
)
async def list_session_files(
    session_id: str,
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> list[dict]:
    """List all files in the session's output/ directory."""
    principal.require_scope("agents:invoke")
    ws = await _verify_ext_session_ownership(session_id, principal)
    return WorkspaceManager.list_output_files(ws)


@router.get(
    "/sessions/{session_id}/files.zip",
    summary="Download all output files as ZIP (external)",
)
async def download_session_files_zip(
    session_id: str,
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> StreamingResponse:
    """Download all files in the session's output/ as a ZIP archive."""
    principal.require_scope("agents:invoke")
    ws = await _verify_ext_session_ownership(session_id, principal)

    files = WorkspaceManager.list_output_files(ws)
    if not files:
        raise NotFoundError(
            code="NO_FILES",
            message="Session has no output files",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in files:
            file_abs = ws.output_dir / entry["path"]
            if file_abs.is_file():
                zf.write(file_abs, entry["path"])

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="session-{session_id}-output.zip"',
        },
    )


@router.get(
    "/sessions/{session_id}/files/{file_path:path}",
    summary="Download a single output file (external)",
)
async def download_session_file(
    session_id: str,
    file_path: str,
    principal: ApiKeyPrincipal = Depends(auth_and_rate_limit),
) -> StreamingResponse:
    """Download a single file from the session's output/ directory."""
    principal.require_scope("agents:invoke")
    ws = await _verify_ext_session_ownership(session_id, principal)

    resolved = WorkspaceManager.safe_resolve_path(ws.output_dir, file_path)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        raise NotFoundError(
            code="FILE_NOT_FOUND",
            message=f"File '{file_path}' not found in session output",
        )

    content_type, _ = mimetypes.guess_type(str(resolved))
    content_type = content_type or "application/octet-stream"

    return StreamingResponse(
        open(resolved, "rb"),
        media_type=content_type,
        headers={
            "Content-Disposition": build_content_disposition(resolved.name),
            "Content-Length": str(resolved.stat().st_size),
        },
    )
