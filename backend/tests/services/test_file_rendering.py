"""file_rendering 图片管线测试 — 降采样/SVG 光栅化/限额/blocks 构造。"""
from __future__ import annotations

import base64
import io

from app.models.file_library import FileConsumerKind, FileRef
from app.services.file_rendering import (
    build_multimodal_content,
    downscale_image_bytes,
    load_images_for_context,
    rasterize_svg,
    sniff_image_mime,
)


def _png_bytes(w: int = 64, h: int = 64, mode: str = "RGB") -> bytes:
    from PIL import Image

    img = Image.new(mode, (w, h), color=(255, 0, 0) if mode == "RGB" else (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80">'
    '<rect width="100" height="80" fill="#3b82f6"/></svg>'
)


# ---------------------------------------------------------------------------
# downscale_image_bytes
# ---------------------------------------------------------------------------


def test_downscale_small_png_passthrough() -> None:
    """达标小图原样返回(无重编码损耗)。"""
    data = _png_bytes()
    res = downscale_image_bytes(data, max_edge=2048, max_size=1024 * 1024)
    assert res is not None
    out, mime = res
    assert out is data
    assert mime == "image/png"


def test_downscale_large_image_shrinks() -> None:
    """超边长图被降采样到 max_edge 内,并重编码为 JPEG。"""
    data = _png_bytes(w=1024, h=768)
    res = downscale_image_bytes(data, max_edge=128, max_size=1024 * 1024)
    assert res is not None
    out, mime = res
    assert mime == "image/jpeg"
    from PIL import Image

    img = Image.open(io.BytesIO(out))
    assert max(img.size) <= 128


def test_downscale_invalid_bytes_returns_none() -> None:
    assert downscale_image_bytes(b"not an image", max_edge=128, max_size=1024) is None


def test_downscale_alpha_png_keeps_png() -> None:
    """带透明通道的图降采样后保持 PNG(不丢 alpha)。"""
    data = _png_bytes(w=300, h=300, mode="RGBA")
    res = downscale_image_bytes(data, max_edge=64, max_size=1024 * 1024)
    assert res is not None
    _, mime = res
    assert mime == "image/png"


# ---------------------------------------------------------------------------
# rasterize_svg
# ---------------------------------------------------------------------------


def test_rasterize_svg_produces_png() -> None:
    out = rasterize_svg(_SVG.encode())
    assert out is not None and out[:8] == b"\x89PNG\r\n\x1a\n"


def test_rasterize_svg_invalid_returns_none() -> None:
    assert rasterize_svg(b"<not-svg>") is None


# ---------------------------------------------------------------------------
# build_multimodal_content
# ---------------------------------------------------------------------------


def test_build_multimodal_content_structure() -> None:
    blocks = build_multimodal_content("看这张图", [
        {"file_id": "f1", "name": "a.png", "mime": "image/png", "data": b"\x89PNG"},
    ])
    assert blocks[0] == {"type": "text", "text": "看这张图"}
    assert blocks[1]["text"] == '[IMAGE file_id="f1" name="a.png"]'
    url = blocks[2]["image_url"]["url"]
    assert url == f"data:image/png;base64,{base64.b64encode(b'\x89PNG').decode()}"


# ---------------------------------------------------------------------------
# load_images_for_context — 限额与分流
# ---------------------------------------------------------------------------


def _img_fref(fid: str, mime: str = "image/png", name: str = "a.png") -> FileRef:
    return FileRef(
        id=fid, owner_user_id="user_1", storage_key=f"user_1/files/{fid}",
        name=name, size=1024, sha256="0" * 64,
        origin_kind=FileConsumerKind.SESSION_MESSAGE, origin_id="sess_1",
        mime_type=mime,
    )


def _patch_loader(monkeypatch, payloads: dict[str, tuple[FileRef, bytes] | None]) -> None:
    from app.services.file_service import FileService

    async def fake_load(self, file_id: str):
        return payloads.get(file_id)

    monkeypatch.setattr(FileService, "load_content", fake_load)


async def test_load_images_basic(monkeypatch) -> None:
    _patch_loader(monkeypatch, {"f1": (_img_fref("f1"), _png_bytes())})
    images, skipped = await load_images_for_context(["f1"])
    assert len(images) == 1 and not skipped
    assert images[0]["file_id"] == "f1"
    assert images[0]["mime"] == "image/png"


async def test_load_images_non_image_ignored(monkeypatch) -> None:
    """非图片 MIME 不进多模态管线(走文本渲染通道)。"""
    _patch_loader(monkeypatch, {"f1": (_img_fref("f1", "text/plain", "a.txt"), b"hello")})
    images, skipped = await load_images_for_context(["f1"])
    assert images == [] and skipped == {}


async def test_load_images_count_cap(monkeypatch) -> None:
    from app.core.config import settings

    _patch_loader(monkeypatch, {
        f"f{i}": (_img_fref(f"f{i}"), _png_bytes()) for i in range(6)
    })
    monkeypatch.setattr(settings, "IMAGE_MAX_PER_TURN", 2, raising=False)
    images, skipped = await load_images_for_context([f"f{i}" for i in range(6)])
    assert len(images) == 2
    assert set(skipped) == {"f2", "f3", "f4", "f5"}
    assert "数量限制" in skipped["f2"]


async def test_load_images_svg_rasterized(monkeypatch) -> None:
    _patch_loader(monkeypatch, {"f1": (_img_fref("f1", "image/svg+xml", "a.svg"), _SVG.encode())})
    images, skipped = await load_images_for_context(["f1"])
    assert len(images) == 1
    assert images[0]["mime"] == "image/png"
    assert images[0]["data"][:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# sniff_image_mime — 声明 MIME 不可信,字节嗅探兜底
# ---------------------------------------------------------------------------


def test_sniff_magic_bytes() -> None:
    assert sniff_image_mime(_png_bytes()) == "image/png"
    assert sniff_image_mime(b"\xff\xd8\xff\xe0abc") == "image/jpeg"
    assert sniff_image_mime(b"GIF89a\x00") == "image/gif"
    assert sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"
    assert sniff_image_mime(_SVG.encode()) == "image/svg+xml"


def test_sniff_falls_back_to_declared_image_mime() -> None:
    """冷门格式嗅不出但声明是图片 → 信任声明交给 Pillow。"""
    assert sniff_image_mime(b"\x00\x01\x02", "image/tiff") == "image/tiff"


def test_sniff_non_image_returns_empty() -> None:
    assert sniff_image_mime(b"hello world", "application/octet-stream") == ""
    assert sniff_image_mime(b"hello world", "text/plain") == ""


async def test_octet_stream_image_still_injected(monkeypatch) -> None:
    """回归:上传时 content_type 缺失(octet-stream)的真图片也要注入——
    此前按声明 MIME 分流会漏掉,占位符谎称"已注入"而模型看不到图。"""
    fref = _img_fref("f1", "application/octet-stream", "photo")
    _patch_loader(monkeypatch, {"f1": (fref, _png_bytes())})
    images, skipped = await load_images_for_context(["f1"])
    assert len(images) == 1 and not skipped
    assert images[0]["mime"] == "image/png"



async def test_render_injected_image_no_placeholder(monkeypatch) -> None:
    """成功注入的图片不渲染 <file> 占位(信息由 [IMAGE 标记]体系承担);
    被省略的图片保留占位(模型拿 file_id 回看的唯一来源)。"""
    from app.services.file_rendering import render_files_by_ids

    _patch_loader(monkeypatch, {
        "f_ok": (_img_fref("f_ok", name="ok.png"), _png_bytes()),
        "f_skip": (_img_fref("f_skip", name="skip.png"), _png_bytes()),
    })
    blocks = await render_files_by_ids(
        ["f_ok", "f_skip"],
        image_notes={"f_skip": "超出单轮图片数量限制，已省略（可用 view_image 工具查看）"},
    )
    # 成功注入的图:无占位块
    assert len(blocks) == 1
    assert 'id="f_skip"' in blocks[0] and "已省略" in blocks[0]
    assert not any('id="f_ok"' in b for b in blocks)
