"""Shared PaddleOCR utilities for the microwire data builder."""

from __future__ import annotations

import inspect
import logging
from functools import lru_cache
from typing import Optional

DEFAULT_LOGGER = "microwire_data_builder"
_MISSING_PADDLE_WARNED = False


@lru_cache(maxsize=1)
def _create_default_ocr() -> "PaddleOCR":
    """Return a cached :class:`~paddleocr.PaddleOCR` instance."""

    from paddleocr import PaddleOCR  # type: ignore[import-not-found]

    base_kwargs = {"lang": "en", "use_angle_cls": True}
    try:
        return PaddleOCR(**base_kwargs)
    except Exception as primary_exc:  # pragma: no cover - defensive
        fallback_exc: Optional[Exception] = None
        try:
            signature = inspect.signature(PaddleOCR.__init__)
        except (TypeError, ValueError):  # pragma: no cover - CPython guard
            signature = None
        if signature is not None and "show_log" in signature.parameters:
            with_flag = dict(base_kwargs)
            with_flag["show_log"] = False
            try:
                return PaddleOCR(**with_flag)
            except Exception as exc:  # pragma: no cover - defensive
                if "show_log" in str(exc):
                    fallback_exc = primary_exc
                else:
                    fallback_exc = exc
        else:
            fallback_exc = primary_exc
        if fallback_exc is not None:
            raise fallback_exc
    raise RuntimeError("Unable to initialise PaddleOCR")


def get_paddle_ocr(logger: Optional[logging.Logger] = None):
    """Return a configured PaddleOCR instance or ``None`` when unavailable."""

    log = logger or logging.getLogger(DEFAULT_LOGGER)
    try:
        return _create_default_ocr()
    except ImportError:
        global _MISSING_PADDLE_WARNED
        if not _MISSING_PADDLE_WARNED:
            log.error(
                "paddleocr is not installed; OCR-dependent features are disabled."
                " Install the pinned PaddlePaddle/PaddleOCR packages from requirements.txt."
            )
            _MISSING_PADDLE_WARNED = True
    except Exception as exc:  # pragma: no cover - defensive
        log.error(
            "Failed to initialise PaddleOCR: %s. Ensure the PaddlePaddle/PaddleOCR"
            " wheels listed in requirements.txt are installed.",
            exc,
        )
    return None


__all__ = ["get_paddle_ocr"]
