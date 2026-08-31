"""File attachment rendering — shared by stream / invoke / history paths.

Renders uploaded files as structured XML blocks that get embedded into the
LLM context so the agent can see file contents without a separate tool call.

Image attachments (``image/*``) are additionally routed to multimodal
``image_url`` blocks via :func:`load_images_for_context` +
:func:`build_multimodal_content` — the XML block here only carries a
placeholder note (plus the file_id the ``view_image`` tool can re-fetch by).
"""
from __future__ import annotations

import base64

_MAX_ATTACHMENT_CHARS = 50_000

_TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml")
_TEXT_MIME_EXACT = {
    "application/javascript", "application/typescript",
    "application/x-yaml", "application/x-sh", "application/sql",
}


def _is_text_mime(mime_type: str, filename: str) -> bool:
    """判断文件是否应视为文本（可安全注入到 LLM 上下文）。"""
    if not mime_type or mime_type == "application/octet-stream":
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return ext in {
            ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".html",
            ".csv", ".tsv", ".py", ".js", ".ts", ".jsx", ".tsx",
            ".sh", ".sql", ".log", ".rst", ".toml", ".ini",
        }
    if mime_type in _TEXT_MIME_EXACT:
        return True
    return any(mime_type.startswith(p) for p in _TEXT_MIME_PREFIXES)


def _render_single_file(
    *, file_id: str, name: str, size: int, mime_type: str, content: str | None,
    truncated: bool = False, unavailable_reason: str | None = None,
) -> str:
    """渲染单个附件为结构化 XML 块。"""
    import html as _html
    attrs = (
        f'id="{file_id}" '
        f'name="{_html.escape(str(name))}" '
        f'size="{size}" '
        f'mime_type="{_html.escape(str(mime_type))}"'
    )
    if content is None:
        note = unavailable_reason or "content unavailable"
        return f"<file {attrs}>\n[{_html.escape(note)}]\n</file>"
    if truncated:
        return (
            f"<file {attrs}>\n"
            f"{content}\n"
            f"[... truncated at {_MAX_ATTACHMENT_CHARS} chars ...]\n"
            f"</file>"
        )
    return f"<file {attrs}>\n{content}\n</file>"


def render_attachments_block(file_blocks: list[str]) -> str:
    """把多个 <file> 块包成 <attachments> 并加提示尾巴。"""
    if not file_blocks:
        return ""
    inner = "\n".join(file_blocks)
    return (
        "\n\n<attachments>\n"
        f"{inner}\n"
        "</attachments>\n\n"
        "提示：如需将附件传给 workflow 的 file 类型参数，使用 <file> 标签的 id 属性值 "
        "（例如 'file_01ABC...'）作为参数值。"
    )


async def render_files_by_ids(
    file_ids: list[str],
    image_notes: dict[str, str] | None = None,
) -> list[str]:
    """根据 file_id 列表加载文件，返回渲染好的 <file> 字符串列表。

    image_notes: file_id → **未注入图片**的省略原因(超限/处理失败)。
    成功注入多模态块的图片不再渲染 <file> 占位——file_id/name 已由
    [IMAGE 标记]块承担(当轮)与降级占位承担(旧轮),重复渲染只会
    误导模型以为还有额外内容可看。只有被省略的图片需要占位:那是模型
    拿 file_id 调 view_image 的唯一信息来源。
    """
    if not file_ids:
        return []
    from app.services.file_service import FileService
    from app.services.file_storage import LocalFileStorage

    file_svc = FileService(storage=LocalFileStorage())
    blocks: list[str] = []
    for fid in file_ids:
        try:
            loaded = await file_svc.load_content(fid)
        except Exception:
            continue
        if loaded is None:
            continue
        fref, data = loaded
        # 嗅探兜底:声明 MIME 不可信(octet-stream 的真图片很常见)。
        sniffed = sniff_image_mime(data, fref.mime_type or "")
        if sniffed == "image/svg+xml":
            # SVG 本质是 XML 文本：源码直接渲染（光栅化结果由
            # load_images_for_context 单独注入 image 块；渲染失败时这里是
            # 模型能看到 SVG 内容的唯一通道）。
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                truncated = len(text) > _MAX_ATTACHMENT_CHARS
                blocks.append(_render_single_file(
                    file_id=fref.id, name=fref.name, size=fref.size,
                    mime_type=sniffed,
                    content=text[:_MAX_ATTACHMENT_CHARS] if truncated else text,
                    truncated=truncated,
                ))
                continue
        if sniffed:
            note = (image_notes or {}).get(fref.id)
            if note is None:
                # 成功注入(或无须说明)的图片:信息由 image 块体系承担,
                # 不渲染占位。
                continue
            blocks.append(_render_single_file(
                file_id=fref.id, name=fref.name, size=fref.size,
                mime_type=fref.mime_type, content=None,
                unavailable_reason=note,
            ))
            continue
        if not _is_text_mime(fref.mime_type, fref.name):
            blocks.append(_render_single_file(
                file_id=fref.id, name=fref.name, size=fref.size,
                mime_type=fref.mime_type, content=None,
                unavailable_reason=f"binary file ({fref.mime_type})",
            ))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            blocks.append(_render_single_file(
                file_id=fref.id, name=fref.name, size=fref.size,
                mime_type=fref.mime_type, content=None,
                unavailable_reason="UTF-8 decode failed",
            ))
            continue
        truncated = len(text) > _MAX_ATTACHMENT_CHARS
        if truncated:
            text = text[:_MAX_ATTACHMENT_CHARS]
        blocks.append(_render_single_file(
            file_id=fref.id, name=fref.name, size=fref.size,
            mime_type=fref.mime_type, content=text, truncated=truncated,
        ))
    return blocks


async def render_files_by_paths(
    file_paths: list[str], workspace_root,
) -> list[str]:
    """Fallback：只有路径时的降级渲染（无 file_id / size / mime_type）。"""
    if not file_paths:
        return []
    from pathlib import Path as _Path

    blocks: list[str] = []
    input_dir = _Path(workspace_root) / "input"
    for rel_path in file_paths:
        abs_path = (input_dir / rel_path).resolve()
        if not str(abs_path).startswith(str(input_dir.resolve())):
            continue
        if not abs_path.is_file():
            continue
        try:
            stat = abs_path.stat()
            data = abs_path.read_bytes()
        except OSError:
            continue
        import mimetypes
        mime, _ = mimetypes.guess_type(abs_path.name)
        mime = mime or "application/octet-stream"
        if not _is_text_mime(mime, abs_path.name):
            blocks.append(_render_single_file(
                file_id="", name=rel_path, size=stat.st_size,
                mime_type=mime, content=None,
                unavailable_reason=f"binary file ({mime})",
            ))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            blocks.append(_render_single_file(
                file_id="", name=rel_path, size=stat.st_size,
                mime_type=mime, content=None,
                unavailable_reason="UTF-8 decode failed",
            ))
            continue
        truncated = len(text) > _MAX_ATTACHMENT_CHARS
        if truncated:
            text = text[:_MAX_ATTACHMENT_CHARS]
        blocks.append(_render_single_file(
            file_id="", name=rel_path, size=stat.st_size,
            mime_type=mime, content=text, truncated=truncated,
        ))
    return blocks


__all__ = [
    "render_attachments_block",
    "render_files_by_ids",
    "render_files_by_paths",
    "load_images_for_context",
    "build_multimodal_content",
    "downscale_image_bytes",
    "rasterize_svg",
    "sniff_image_mime",
]


# ---------------------------------------------------------------------------
# Multimodal image pipeline — bitmaps via Pillow downscale, SVG via PyMuPDF
# rasterization; both zero new dependencies (already runtime deps).
# ---------------------------------------------------------------------------

def downscale_image_bytes(
    data: bytes, *, max_edge: int, max_size: int,
) -> tuple[bytes, str] | None:
    """降采样/重编码图片字节，返回 (bytes, mime)；失败返回 None。

    - 已达标（边长/体积内且 provider 支持的格式）→ 原样返回
    - 超边长/体积 → thumbnail 降采样；带透明 → PNG，否则 JPEG(q85)
    - 仍超体积 → JPEG 逐级降质（70/55/40），再不行缩边（1024→512）重试，
      最终仍超 → None
    - provider 不支持的位图格式（bmp/tiff 等）→ 统一转 PNG
    """
    from io import BytesIO

    from PIL import Image  # pillow 已是运行时依赖（rapidocr 传递依赖）

    try:
        img: Image.Image = Image.open(BytesIO(data))
        img.load()
    except Exception:
        return None

    fmt = (img.format or "").lower()
    supported = ("png", "jpeg", "webp", "gif")
    if fmt in supported and max(img.size) <= max_edge and len(data) <= max_size:
        mime = {"jpeg": "image/jpeg", "gif": "image/gif"}.get(fmt, f"image/{fmt}")
        return data, mime

    has_alpha = img.mode == "RGBA" or (img.mode == "P" and "transparency" in img.info)

    def _shrink_and_encode(edge: int, alpha: bool) -> tuple[bytes, str] | None:
        """按给定边长缩到 max_size 内;逐级降质,失败返回 None。"""
        im = img.convert("RGBA" if alpha else "RGB")
        im.thumbnail((edge, edge), Image.LANCZOS)
        if alpha:
            buf = BytesIO()
            im.save(buf, "PNG")
            out = buf.getvalue()
            if len(out) <= max_size:
                return out, "image/png"
            # 透明 PNG 仍超限:拍平到白底继续压 JPEG。
            from PIL import Image as _Image

            bg = _Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        for q in (85, 70, 55, 40):
            buf = BytesIO()
            im.save(buf, "JPEG", quality=q)
            out = buf.getvalue()
            if len(out) <= max_size:
                return out, "image/jpeg"
        return None

    for edge in (max_edge, 1024, 512):
        res = _shrink_and_encode(min(edge, max_edge), has_alpha)
        if res is not None:
            return res
    return None


def rasterize_svg(data: bytes) -> bytes | None:
    """SVG → PNG 字节（PyMuPDF）。MuPDF 的 SVG 解析不完整（渐变/滤镜等
    高级特性可能失真），失败返回 None 由调用方降级文本源码渲染。"""
    try:
        import fitz  # type: ignore[import-untyped]  # pymupdf 已是运行时依赖

        doc = fitz.open(stream=data, filetype="svg")
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        out = pix.tobytes("png")
        return out if out else None
    except Exception:
        return None


def sniff_image_mime(data: bytes, declared: str = "") -> str:
    """按 magic bytes 判定图片真实类型;非图片字节返回 ""。

    上传时 FileRef.mime_type 来自 HTTP content_type,客户端缺失时会被存成
    application/octet-stream —— 仅按声明 MIME 分流会把真图片漏掉(现象:
    占位符说"已注入"但模型看不到图,只能调 view_image,而 view_image 也按
    声明 MIME 拒绝)。字节嗅探是唯一可信源,声明值仅作 fallback。
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or head.startswith(b"<?xml") and b"<svg" in head:
        return "image/svg+xml"
    if not declared.startswith("image/"):
        return ""
    # 声明是图片但字节嗅不出(如冷门格式):信任声明,交给 Pillow 试开。
    return declared


async def load_images_for_context(
    file_ids: list[str] | None,
) -> tuple[list[dict], dict[str, str]]:
    """加载图片附件并处理为可注入 LLM 的载荷。

    images 元素为 [{"file_id","name","mime","data": bytes}],调用方构造
    image_url blocks(要求主模型端到端多模态;纯文本主模型由模型自行调用
    多模态工具做语义转化,平台不自动处理)。

    Returns:
        (images, skipped)：skipped 为 {file_id: 省略原因}——**每个未注入的
        图片都必须有原因**,绝不让占位符谎称"已注入"。受 IMAGE_MAX_PER_TURN /
        IMAGE_MAX_SIZE_BYTES / IMAGE_MAX_TOTAL_BYTES 限额。
    """
    if not file_ids:
        return [], {}
    import structlog

    from app.core.config import settings
    from app.services.file_service import FileService
    from app.services.file_storage import LocalFileStorage

    log = structlog.get_logger(__name__)
    file_svc = FileService(storage=LocalFileStorage())
    images: list[dict] = []
    skipped: dict[str, str] = {}
    total = 0
    for fid in file_ids:
        try:
            loaded = await file_svc.load_content(fid)
        except Exception as exc:
            log.warning("image_load_failed", file_id=fid, error=str(exc))
            continue  # 连字节都读不到:render 侧按 binary 占位,不谎称注入
        if loaded is None:
            continue
        fref, data = loaded
        mime = sniff_image_mime(data, fref.mime_type or "")
        if not mime:
            continue  # 真非图片:走文本/占位渲染通道
        if mime == "image/svg+xml":
            png = rasterize_svg(data)
            if png is None:
                # 光栅化失败:源码已由 render_files_by_ids 文本通道渲染,
                # 这里不进 skipped(不算"图片被省略")。
                continue
            data, mime = png, "image/png"
        else:
            res = downscale_image_bytes(
                data,
                max_edge=settings.IMAGE_MAX_EDGE,
                max_size=settings.IMAGE_MAX_SIZE_BYTES,
            )
            if res is None:
                skipped[fid] = "图片处理失败或超出大小限制，已省略（可用 view_image 工具查看）"
                continue
            data, mime = res
        if len(images) >= settings.IMAGE_MAX_PER_TURN:
            skipped[fid] = "超出单轮图片数量限制，已省略（可用 view_image 工具查看）"
            continue
        if total + len(data) > settings.IMAGE_MAX_TOTAL_BYTES:
            skipped[fid] = "超出单轮图片总体积限制，已省略（可用 view_image 工具查看）"
            continue
        total += len(data)
        images.append({"file_id": fid, "name": fref.name, "mime": mime, "data": data})
    if file_ids:
        log.info(
            "images_for_context",
            requested=len(file_ids), injected=len(images),
            skipped={k: v[:20] for k, v in skipped.items()},
        )
    return images, skipped


def build_multimodal_content(text_content: str, images: list[dict]) -> list[dict]:
    """拼多模态 content blocks：text 块 + 每张图一对 [IMAGE 标记]+image_url。

    标记块（`[IMAGE file_id="..." name="..."]`）是 harness 降级图片时
    反查 file_id/name 的依据，与 image_url 块成对出现、不可省略。
    """
    blocks: list[dict] = [{"type": "text", "text": text_content}]
    for img in images:
        b64 = base64.b64encode(img["data"]).decode()
        blocks.append({
            "type": "text",
            "text": f'[IMAGE file_id="{img["file_id"]}" name="{img["name"]}"]',
        })
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f'data:{img["mime"]};base64,{b64}'},
        })
    return blocks
