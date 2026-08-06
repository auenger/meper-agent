"""ImageExtractor — extract and recognize images from PDF pages.

Handles three page scenarios:
  - Pure text page: no images, nothing to do.
  - Scan-only page (empty/thin text layer): render the whole page to an image,
    recognize via vision model or OCR, inject the text into the TextBlock.
  - Mixed page (text layer + embedded images): extract each image object,
    store it, and recognize via vision model (description) or OCR (text).

Extracted images are stored via FileService (FileRef) so they can be viewed
later in the chunk viewer / search results. Each chunk carries the list of
associated ``image_ref_ids``.

Recognition priority: vision model (if configured) → OCR (if enabled) → skip.
"""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import HumanMessage  # noqa: F401

    from app.engine.kb.vector.ocr_engine import RapidOCREngine
    from app.services.file_service import FileService


# Minimum text length (chars) for a page to be considered "has text layer".
# Below this threshold the page is treated as a scan-only page.
_SCAN_TEXT_THRESHOLD = 20


class ImageExtractor:
    """Extract and recognize images from PDF pages during indexing.

    Constructed once per document in ``_index_async``. Holds references to
    the vision client, OCR engine, and file service so each page processing
    can store images and recognize their content.
    """

    def __init__(
        self,
        *,
        vision_client: BaseChatModel | None,
        ocr_engine: RapidOCREngine | None,
        file_service: FileService,
        doc_id: str,
        owner_id: str,
        extract_images: bool = True,
    ) -> None:
        self._vision = vision_client
        self._ocr = ocr_engine
        self._file_service = file_service
        self._doc_id = doc_id
        self._owner_id = owner_id or "system"
        self._extract_images = extract_images

    @property
    def has_recognizer(self) -> bool:
        """Whether any recognition backend is available."""
        return self._vision is not None or self._ocr is not None

    async def process_scan_page(self, page, page_num: int) -> tuple[str, list[str]]:
        """Process a scan-only page (no text layer).

        Renders the whole page to a PNG image, stores it, and recognizes text.

        Returns:
            ``(recognized_text, image_ref_ids)`` — text may be empty if
            recognition fails or nothing was found.
        """
        # Render page to a pixmap at 2x zoom for OCR quality.
        pixmap = self._render_page(page, zoom=2.0)
        if pixmap is None:
            return "", []

        png_bytes = pixmap.tobytes("png")
        image_ref_ids: list[str] = []

        if self._extract_images:
            ref_id = await self._store_image(
                png_bytes,
                f"page_{page_num}.png",
                mime_type="image/png",
            )
            if ref_id:
                image_ref_ids.append(ref_id)

        text = await self._recognize(png_bytes, is_scan=True, page_num=page_num)
        return text, image_ref_ids

    async def process_page_images(self, page, page_num: int) -> tuple[str, list[str]]:
        """Process embedded images on a page that also has a text layer.

        Extracts each image object (``page.get_images``), stores it, and
        generates a description (vision) or text (OCR).

        Returns:
            ``(descriptions_text, image_ref_ids)`` — concatenated descriptions
            for all recognized images on this page.
        """
        image_list = page.get_images(full=True)
        if not image_list:
            return "", []

        descriptions: list[str] = []
        image_ref_ids: list[str] = []

        for img_index, img_info in enumerate(image_list):
            xref = img_info[0]
            try:
                base_image = page.parent.extract_image(xref)
            except Exception as exc:
                logger.debug("pdf_image_extract_failed", page=page_num, xref=xref, error=str(exc))
                continue

            img_bytes = base_image.get("image")
            if not img_bytes:
                continue

            img_ext = base_image.get("ext", "png")
            mime = f"image/{img_ext}" if img_ext != "jpg" else "image/jpeg"

            if self._extract_images:
                ref_id = await self._store_image(
                    img_bytes,
                    f"page_{page_num}_img_{img_index + 1}.{img_ext}",
                    mime_type=mime,
                )
                if ref_id:
                    image_ref_ids.append(ref_id)

            desc = await self._recognize(img_bytes, is_scan=False, page_num=page_num)
            if desc:
                descriptions.append(desc)

        return "\n".join(descriptions), image_ref_ids

    # ── internal helpers ──────────────────────────────────────────────

    def _render_page(self, page, zoom: float = 2.0):
        """Render a PDF page to a PyMuPDF Pixmap."""
        import fitz  # type: ignore[import-untyped]

        try:
            matrix = fitz.Matrix(zoom, zoom)
            return page.get_pixmap(matrix=matrix, alpha=False)
        except Exception as exc:
            logger.warning("pdf_page_render_failed", error=str(exc))
            return None

    async def _store_image(self, data: bytes, filename: str, mime_type: str) -> str | None:
        """Store an image via FileService, return the FileRef id."""
        from app.models.file_library import FileConsumerKind

        try:
            fref = await self._file_service.create(
                data=data,
                filename=filename,
                mime_type=mime_type,
                owner_user_id=self._owner_id,
                origin_kind=FileConsumerKind.KNOWLEDGE_BASE,
                origin_id=self._doc_id,
            )
            await self._file_service.add_usage(
                fref.id, FileConsumerKind.KNOWLEDGE_BASE, self._doc_id
            )
            return fref.id
        except Exception as exc:
            logger.warning("image_store_failed", filename=filename, error=str(exc))
            return None

    async def _recognize(self, image_bytes: bytes, *, is_scan: bool, page_num: int) -> str:
        """Recognize text from image bytes via vision model or OCR.

        Priority: vision model → OCR → empty string.
        """
        if self._vision is not None:
            return await self._recognize_via_vision(image_bytes, is_scan=is_scan)
        if self._ocr is not None:
            return self._recognize_via_ocr(image_bytes)
        return ""

    async def _recognize_via_vision(self, image_bytes: bytes, *, is_scan: bool) -> str:
        """Send image to the vision LLM for text extraction / description."""
        from langchain_core.messages import HumanMessage

        if is_scan:
            prompt = (
                "请识别这张文档图片中的所有文字内容，保持原始的段落和换行结构。"
                "只输出识别到的文字，不要添加解释。"
            )
        else:
            prompt = (
                "请描述这张图片的内容。如果是图表或示意图，请详细说明图表的数据和含义；"
                "如果是截图或照片，请描述其中的关键信息。用中文回答，简洁明了。"
            )

        img_b64 = base64.b64encode(image_bytes).decode()
        # Detect format from first bytes for the data URL MIME.
        mime = "image/png"
        if image_bytes[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
            ]
        )
        try:
            response = await self._vision.ainvoke([message])  # type: ignore[union-attr]
            # Extract text from the AIMessage response.
            content = response.content if hasattr(response, "content") else str(response)
            if isinstance(content, list):
                # Multimodal response may be a list of content blocks.
                parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
                return "\n".join(p for p in parts if p).strip()
            return str(content).strip()
        except Exception as exc:
            logger.warning("vision_recognize_failed", is_scan=is_scan, error=str(exc))
            # Fall back to OCR if vision fails.
            if self._ocr is not None:
                return self._recognize_via_ocr(image_bytes)
            return ""

    def _recognize_via_ocr(self, image_bytes: bytes) -> str:
        """Run local OCR (RapidOCR) on image bytes."""
        try:
            return self._ocr.recognize(image_bytes)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("ocr_recognize_failed", error=str(exc))
            return ""


def is_scan_page(text: str) -> bool:
    """Heuristic: a page with very little extractable text is likely a scan."""
    return len(text.strip()) < _SCAN_TEXT_THRESHOLD


__all__ = ["ImageExtractor", "is_scan_page"]
