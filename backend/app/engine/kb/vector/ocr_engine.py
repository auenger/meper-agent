"""RapidOCR engine wrapper — local OCR for image/scan PDF recognition.

RapidOCR (``rapidocr-onnxruntime``) is a lightweight OCR package based on
PaddleOCR models running via ONNX Runtime. It requires no system-level
dependencies (no tesseract), installs via pip, and supports Chinese + English
by default.

This module is imported lazily by ``factory.get_ocr_engine()`` so the model
is only loaded on first use, keeping app startup fast.
"""
from __future__ import annotations

from typing import Any

from loguru import logger


class RapidOCREngine:
    """Thin async-compatible wrapper around RapidOCR.

    RapidOCR's ``__call__`` is synchronous and CPU-bound, so we keep a single
    engine instance (model loaded once) and expose a simple ``recognize``
    method. Callers run it in the worker process where blocking is acceptable.
    """

    def __init__(self, languages: str = "ch") -> None:
        # Import is deferred to __init__ (called only when OCR is enabled).
        from rapidocr_onnxruntime import RapidOCR

        # RapidOCR accepts language hints; "ch" = Chinese + English (default).
        self._ocr = RapidOCR()
        self._languages = languages
        logger.debug("rapidocr_initialized", languages=languages)

    def recognize(self, image: bytes | Any) -> str:
        """Run OCR on an image, returning concatenated recognized text.

        Args:
            image: image bytes (PNG/JPEG) OR a numpy array (from PyMuPDF pixmap).

        Returns:
            All recognized text fragments joined by newlines. Empty string if
            nothing was recognized or the image has no text.
        """
        try:
            result, _elapsed = self._ocr(image)
        except Exception as exc:
            logger.warning("ocr_recognize_failed", error=str(exc))
            return ""

        if not result:
            return ""

        # RapidOCR returns a list of [box, text, score] tuples.
        texts: list[str] = []
        for item in result:
            if item and len(item) >= 2 and item[1]:
                texts.append(str(item[1]).strip())
        return "\n".join(t for t in texts if t)


__all__ = ["RapidOCREngine"]
