"""Shared OCR utilities for the microwire data builder."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, Optional

DEFAULT_LOGGER = "microwire_data_builder"
_MISSING_TESSERACT_WARNED = False


def _candidate_from_path(candidate: Path) -> Iterator[Path]:
    exe_names = ("tesseract.exe", "tesseract")
    try:
        if candidate.is_file():
            yield candidate
            return
    except OSError:
        return
    if candidate.is_dir():
        for name in exe_names:
            exe_path = candidate / name
            try:
                if exe_path.is_file():
                    yield exe_path
            except OSError:
                continue


def _iter_candidate_paths() -> Iterable[Path]:
    """Yield common locations of the Tesseract executable."""

    env_keys = ("TESSERACT_CMD", "TESSERACT_PATH", "TESSDATA_PREFIX")
    for key in env_keys:
        value = os.environ.get(key)
        if not value:
            continue
        for resolved in _candidate_from_path(Path(value)):
            yield resolved

    hinted = os.environ.get("LOCALAPPDATA")
    if hinted:
        for resolved in _candidate_from_path(
            Path(hinted) / "Programs" / "Tesseract-OCR"
        ):
            yield resolved

    program_dirs = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    for root in program_dirs:
        if not root:
            continue
        for resolved in _candidate_from_path(Path(root) / "Tesseract-OCR"):
            yield resolved

    unix_hints = (
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/usr/local/opt/tesseract/bin"),
        Path("/opt/homebrew/bin"),
        Path.home() / "opt" / "homebrew" / "bin",
        Path("/opt/local/bin"),
    )
    for root in unix_hints:
        for resolved in _candidate_from_path(root):
            yield resolved


@lru_cache(maxsize=1)
def _resolve_tesseract_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    which = shutil.which("tesseract")
    if which:
        path = Path(which)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if path.is_file() and resolved not in seen:
            candidates.append(path)
            seen.add(resolved)
    for candidate in _iter_candidate_paths():
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        try:
            is_file = candidate.is_file()
        except OSError:
            continue
        if not is_file:
            continue
        candidates.append(candidate)
        seen.add(resolved)
    return tuple(candidates)


def ensure_tesseract_available(
    pytesseract_module, logger: Optional[logging.Logger] = None
) -> bool:
    """Ensure *pytesseract_module* can reach a Tesseract executable.

    Returns True when the module reports a working Tesseract binary. When
    resolution fails, False is returned and a warning is emitted (once per
    interpreter session).
    """

    log = logger or logging.getLogger(DEFAULT_LOGGER)
    getter = getattr(pytesseract_module, "get_tesseract_version", None)
    if getter is None:
        log.warning(
            "pytesseract is installed but does not expose get_tesseract_version()"
        )
        return False

    tesseract_not_found = getattr(
        pytesseract_module, "TesseractNotFoundError", RuntimeError
    )
    try:
        getter()
        return True
    except tesseract_not_found:
        pass

    module_root = getattr(pytesseract_module, "pytesseract", pytesseract_module)
    for candidate in _resolve_tesseract_candidates():
        try:
            module_root.tesseract_cmd = str(candidate)
        except Exception:
            continue
        try:
            pytesseract_module.tesseract_cmd = str(candidate)
        except Exception:
            pass
        try:
            getter()
            return True
        except tesseract_not_found:
            continue
        except Exception:
            continue

    global _MISSING_TESSERACT_WARNED
    if not _MISSING_TESSERACT_WARNED:
        hint = (
            "Tesseract OCR executable is not available. Install it and ensure it is on PATH."
        )
        if sys.platform == "darwin":
            hint += " (e.g. `brew install tesseract`)"
        log.warning(hint)
        _MISSING_TESSERACT_WARNED = True
    return False


__all__ = ["ensure_tesseract_available"]
