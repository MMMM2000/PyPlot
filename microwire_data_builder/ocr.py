"""Shared PaddleOCR utilities for the microwire data builder."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

DEFAULT_LOGGER = "microwire_data_builder"
_MISSING_PADDLE_WARNED = False


@lru_cache(maxsize=1)
def _create_default_ocr() -> "PaddleOCR":
    """Return a cached :class:`~paddleocr.PaddleOCR` instance."""

    from paddleocr import PaddleOCR  # type: ignore[import-not-found]

    return PaddleOCR(lang="en", use_angle_cls=True, show_log=False)


def get_paddle_ocr(logger: Optional[logging.Logger] = None):
    """Return a configured PaddleOCR instance or ``None`` when unavailable."""

    log = logger or logging.getLogger(DEFAULT_LOGGER)
    try:
        return _create_default_ocr()
    except ImportError:
        global _MISSING_PADDLE_WARNED
        if not _MISSING_PADDLE_WARNED:
            log.warning(
                "paddleocr is not installed; OCR-dependent features are disabled"
            )
            _MISSING_PADDLE_WARNED = True
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Failed to initialise PaddleOCR: %s", exc)
    return None


__all__ = ["get_paddle_ocr"]
