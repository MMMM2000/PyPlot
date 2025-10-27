"""Shared PaddleOCR utilities for the microwire data builder."""

from __future__ import annotations

import inspect
import logging
import os
import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from paddleocr import PaddleOCR

DEFAULT_LOGGER = "microwire_data_builder"
_MISSING_PADDLE_WARNED = False

# Environment variables that influence PaddleOCR/PaddleX cache locations. We map
# each one to a known ASCII-only directory so Windows accounts with diacritics
# do not break lazy model downloads.
_CACHE_ENV_VARS: dict[str, str] = {
    "PADDLE_HOME": "paddle_home",
    "PADDLE_MODEL_HOME": "paddle_models",
    "PADDLEX_HOME": "paddlex",
    "PADDLEX_OFFICIAL_MODEL_HOME": "paddlex_official_models",
    "PADDLEOCR_HOME": "paddleocr",
    "PPOCR_MODEL_CACHE_DIR": "ppocr_models",
}


def _prepare_paddle_cache() -> Path:
    """Return a filesystem location that is ASCII-safe for PaddleOCR caches."""

    temp_root = Path(tempfile.gettempdir())
    try:
        str(temp_root).encode("ascii")
    except UnicodeEncodeError:
        if os.name == "nt":
            drive = temp_root.drive or Path.cwd().drive or "C:"
            drive_path = Path(drive + os.sep)
            base = drive_path / "microwire_paddle_cache"
        else:
            base = Path("/tmp") / "microwire_paddle_cache"
    else:
        base = temp_root / "microwire_paddle_cache"

    cache_root = base.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    for env_var, subdir in _CACHE_ENV_VARS.items():
        target = cache_root / subdir
        target.mkdir(parents=True, exist_ok=True)
        os.environ[env_var] = str(target)

    return cache_root


_CACHE_ROOT = _prepare_paddle_cache()


def _purge_corrupted_cache(cache_root: Path, extra_path: Path | None = None) -> None:
    """Remove cache artefacts that might have been partially downloaded."""

    def _purge(path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except FileNotFoundError:  # pragma: no cover - race condition guard
                return

    _purge(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    for env_var, subdir in _CACHE_ENV_VARS.items():
        target = cache_root / subdir
        target.mkdir(parents=True, exist_ok=True)
        os.environ[env_var] = str(target)

    if extra_path is not None:
        _purge(extra_path)


_CORRUPTED_MODEL_RE = re.compile(r"Cannot open file ([^,]+)")


def _looks_like_corrupted_model(exc: Exception) -> tuple[bool, Path | None]:
    message = str(exc)
    if "Cannot open file" in message and "inference.json" in message:
        match = _CORRUPTED_MODEL_RE.search(message)
        if match:
            candidate = match.group(1).strip().strip("`\"")
            try:
                return True, Path(candidate).resolve()
            except OSError:  # pragma: no cover - Windows path edge cases
                return True, None
        return True, None
    return False, None


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

    try:
        signature = inspect.signature(PaddleOCR.__init__)
    except (TypeError, ValueError):  # pragma: no cover - CPython guard
        signature = None

    try:
        return _initialise_paddle(PaddleOCR, signature)
    except Exception as exc:
        corrupted, path = _looks_like_corrupted_model(exc)
        if corrupted:
            extra = path.parent if path is not None else None
            _purge_corrupted_cache(_CACHE_ROOT, extra_path=extra)
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
