"""Shared path helpers for PyPlot."""

from __future__ import annotations

import datetime
import os
from pathlib import Path

from PyQt6 import QtCore

from .settings import get_settings


def download_dir() -> str:
    """Return the user's default download directory."""

    return str(Path.home() / "Downloads")


def sample_dir() -> str:
    """Return the sample data directory if it exists."""

    sample = Path(__file__).resolve().parents[1] / "sample_data"
    return str(sample) if sample.exists() else download_dir()


def _settings() -> QtCore.QSettings:
    return get_settings()


def get_last_output_dir(default: str | None = None, *, key: str | None = None) -> str:
    """Return the last output directory stored for ``key``."""

    s = _settings()
    if key:
        return s.value(f"{key}_last_output_dir", default or download_dir(), type=str)
    return s.value("last_output_dir", default or download_dir(), type=str)


def set_last_output_dir(path: str, *, key: str | None = None) -> None:
    """Persist ``path`` as the last output directory for ``key``."""

    s = _settings()
    if key:
        s.setValue(f"{key}_last_output_dir", path)
    else:
        s.setValue("last_output_dir", path)


def prepare_output_dir(base: str, script: str, create_sub: bool) -> str:
    """Return a directory ready for exports, creating it when required."""

    path = Path(base or download_dir())
    if create_sub:
        stamp = datetime.date.today().isoformat()
        folder = f"{script} data {stamp}"
        path = path / folder
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_last_used_dir(key: str, default: str | None = None) -> str:
    """Return the last directory used for ``key``."""

    s = _settings()
    return s.value(f"{key}_last_used_dir", default or download_dir(), type=str)


def set_last_used_dir(key: str, path: str) -> None:
    """Persist ``path`` as the last directory used for ``key``."""

    _settings().setValue(f"{key}_last_used_dir", path)


__all__ = [
    "download_dir",
    "sample_dir",
    "get_last_output_dir",
    "set_last_output_dir",
    "prepare_output_dir",
    "get_last_used_dir",
    "set_last_used_dir",
]
