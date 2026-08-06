"""PDF parser — extracts text page-by-page via PyMuPDF (``fitz``).

Streaming one page at a time avoids loading an entire large PDF into memory.
Each page becomes a :class:`TextBlock` carrying its 1-based page number so
citations can point back to the source page.

When an :class:`ImageExtractor` is provided, images and scan-only pages are
also processed:
  - Scan-only pages (empty/thin text layer): the whole page is rendered to an
    image, recognized via vision/OCR, and the recognized text is injected.
  - Mixed pages (text + embedded images): each image is extracted, stored,
    and recognized (vision description or OCR text), appended to the page text.

Without an ImageExtractor, behavior is unchanged (pure text-layer extraction).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.engine.kb.vector.parser.base import ParseResult, TextBlock

if TYPE_CHECKING:
    from app.engine.kb.vector.parser.image_extractor import ImageExtractor


def parse_pdf(file_bytes: bytes) -> ParseResult:
    """Parse PDF text (pure text-layer extraction, no image processing).

    Kept for synchronous callers and as the default path when no image
    extractor is configured.
    """
    import fitz  # type: ignore[import-untyped]  # PyMuPDF

    blocks: list[TextBlock] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            text = text.strip()
            if text:
                blocks.append(TextBlock(text=text, page=page_num))
        total = doc.page_count
    return ParseResult(blocks=blocks, file_type="pdf", total_pages=total)


async def parse_pdf_with_images(
    file_bytes: bytes,
    image_extractor: ImageExtractor,
) -> ParseResult:
    """Parse PDF with image/scan recognition.

    For each page:
    1. Extract the text layer (``get_text("text")``).
    2. If the text layer is thin (scan page) and a recognizer is available,
       render the page to an image, recognize it, and use the recognized text.
    3. If the page has a text layer AND embedded images, extract and recognize
       each image, appending descriptions to the page text.
    4. Store all extracted images via FileService, collecting ``image_ref_ids``.
    """
    import fitz  # type: ignore[import-untyped]  # PyMuPDF

    from app.engine.kb.vector.parser.image_extractor import is_scan_page

    blocks: list[TextBlock] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc, start=1):
            text = (page.get_text("text") or "").strip()
            image_ref_ids: list[str] = []

            if is_scan_page(text) and image_extractor.has_recognizer:
                # Scan-only page: render + recognize the whole page.
                recognized, img_ids = await image_extractor.process_scan_page(page, page_num)
                image_ref_ids = img_ids
                if recognized:
                    text = recognized
                # If recognition returned nothing, text stays empty → page skipped.
            elif text:
                # Page has text — also check for embedded images.
                if image_extractor.has_recognizer:
                    descriptions, img_ids = await image_extractor.process_page_images(page, page_num)
                    image_ref_ids = img_ids
                    if descriptions:
                        text = text + "\n\n" + descriptions

            if text:
                blocks.append(
                    TextBlock(text=text, page=page_num, image_ref_ids=image_ref_ids)
                )
        total = doc.page_count
    return ParseResult(blocks=blocks, file_type="pdf", total_pages=total)


__all__ = ["parse_pdf", "parse_pdf_with_images"]
