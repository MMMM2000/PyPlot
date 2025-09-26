"""Shared OCR utilities for the microwire data builder."""

from __future__ import annotations

import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_LOGGER = "microwire_data_builder"


def _iter_candidate_paths() -> Iterable[Path]:
    """Yield common locations of the Tesseract executable."""

    env_keys = ("TESSERACT_CMD", "TESSERACT_PATH", "TESSDATA_PREFIX")
    for key in env_keys:
        value = os.environ.get(key)
        if value:
            candidate = Path(value)
            if candidate.is_file():
                yield candidate
            else:
                exe = candidate / "tesseract.exe"
                if exe.is_file():
                    yield exe
    hinted = os.environ.get("LOCALAPPDATA")
    if hinted:
        candidate = Path(hinted) / "Programs" / "Tesseract-OCR" / "tesseract.exe"
        if candidate.is_file():
            yield candidate
    program_dirs = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    for root in program_dirs:
        if not root:
            continue
        candidate = Path(root) / "Tesseract-OCR" / "tesseract.exe"
        if candidate.is_file():
            yield candidate
    if os.name != "nt":
        for path in ("/usr/bin/tesseract", "/usr/local/bin/tesseract"):
            candidate = Path(path)
            if candidate.is_file():
                yield candidate


@lru_cache(maxsize=1)
def _resolve_tesseract_command() -> Optional[Path]:
    which = shutil.which("tesseract")
    if which:
        return Path(which)
    for candidate in _iter_candidate_paths():
        if candidate.is_file():
            return candidate
    return None


def ensure_tesseract_available(pytesseract_module, logger: Optional[logging.Logger] = None) -> bool:
    """Ensure *pytesseract_module* can reach a Tesseract executable.

    Returns True when the module reports a working Tesseract binary. When
    resolution fails, False is returned and a warning is emitted (once per
    interpreter session).
    """

    log = logger or logging.getLogger(DEFAULT_LOGGER)
    getter = getattr(pytesseract_module, "get_tesseract_version", None)
    if getter is None:
        log.warning("pytesseract is installed but does not expose get_tesseract_version()")
        return False

    tesseract_not_found = getattr(pytesseract_module, "TesseractNotFoundError", RuntimeError)
    try:
        getter()
        return True
    except tesseract_not_found:
        pass

    candidate = _resolve_tesseract_command()
    if candidate is not None:
        try:
            pytesseract_module.pytesseract.tesseract_cmd = str(candidate)
            getter()
            return True
        except Exception:
            pass

    log.warning(
        "Tesseract OCR executable is not available. Install it and ensure it is on PATH."
    )
    return False


__all__ = ["ensure_tesseract_available"]


