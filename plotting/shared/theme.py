"""Shared theming helpers for PyPlot."""

from __future__ import annotations

from PyQt6 import QtWidgets

from .toolkit import apply_system_theme


def ensure_app_theme(app: QtWidgets.QApplication) -> None:
    """Apply the stored theme preference to ``app``."""

    apply_system_theme(app)


__all__ = ["ensure_app_theme", "apply_system_theme"]
