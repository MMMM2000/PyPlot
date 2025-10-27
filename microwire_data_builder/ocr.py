"""Shared PaddleOCR utilities for the microwire data builder."""

from __future__ import annotations

import inspect
import logging
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from paddleocr import PaddleOCR

DEFAULT_LOGGER = "microwire_data_builder"
_MISSING_PADDLE_WARNED = False
_CACHE_ENV_VARS = ("PADDLEOCR_HOME", "PPOCR_MODEL_CACHE_DIR")


def _prepare_paddle_cache() -> Path:
    """Return a filesystem location that is ASCII-safe for PaddleOCR caches."""

    cache_root = Path(tempfile.gettempdir()) / "microwire_paddleocr_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    for env_var in _CACHE_ENV_VARS:
        os.environ.setdefault(env_var, str(cache_root))
    return cache_root


def _purge_corrupted_cache(cache_root: Path) -> None:
    """Remove cache artefacts that might have been partially downloaded."""

    if not cache_root.exists():
        return

    for child in cache_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:  # pragma: no cover - race condition guard
                continue


def _looks_like_corrupted_model(exc: Exception) -> bool:
    message = str(exc)
    return "Cannot open file" in message and "inference.json" in message


def _initialise_paddle(
    paddle_ctor: type["PaddleOCR"], signature: inspect.Signature | None
) -> "PaddleOCR":
    last_exc: Optional[Exception] = None
    for candidate in _candidate_kwargs(signature):
        if signature is not None:
            filtered = {
                key: value
                for key, value in candidate.items()
                if key in signature.parameters
            }
        else:
            filtered = candidate
        try:
            return paddle_ctor(**filtered)
        except Exception as exc:  # pragma: no cover - defensive
            last_exc = exc
            continue

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unable to initialise PaddleOCR")


def _candidate_kwargs(signature: inspect.Signature | None) -> list[dict[str, object]]:
    """Return a list of progressively simpler PaddleOCR kwargs."""

    supported: set[str] = set(signature.parameters) if signature is not None else set()

    tuned: dict[str, object] = {
        "lang": "en",
        "use_angle_cls": True,
        "det_db_box_thresh": 0.18,
        "det_db_unclip_ratio": 2.6,
        "det_limit_side_len": 4096,
        "drop_score": 0.1,
        "max_text_length": 96,
        "rec_algorithm": "SVTR_LCNet",
    }
    if "show_log" in supported:
        tuned["show_log"] = False

    baseline = {"lang": "en", "use_angle_cls": True}
    if "show_log" in supported:
        baseline["show_log"] = False

    return [tuned, baseline, {"lang": "en"}]


@lru_cache(maxsize=1)
def _create_default_ocr() -> "PaddleOCR":
    """Return a cached :class:`~paddleocr.PaddleOCR` instance."""

    from paddleocr import PaddleOCR  # type: ignore[import-not-found]

    cache_root = _prepare_paddle_cache()

    try:
        signature = inspect.signature(PaddleOCR.__init__)
    except (TypeError, ValueError):  # pragma: no cover - CPython guard
        signature = None

    try:
        return _initialise_paddle(PaddleOCR, signature)
    except Exception as exc:
        if _looks_like_corrupted_model(exc):
            _purge_corrupted_cache(cache_root)
            return _initialise_paddle(PaddleOCR, signature)
        raise


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
