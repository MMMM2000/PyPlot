"""Shared theming helpers for PyPlot."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from .toolkit import apply_system_theme

_QT_MESSAGE_HANDLER_INSTALLED = False
_QT_MESSAGE_HANDLER = None
_OFFSCREEN_FONT_LOADED = False


def _configure_windows_offscreen_font_dir() -> None:
    """Point Qt's offscreen platform at Windows fonts before QApplication starts."""

    platform = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    if platform != "offscreen" or not sys.platform.startswith("win"):
        return
    if os.environ.get("QT_QPA_FONTDIR"):
        return
    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if font_dir.exists():
        os.environ["QT_QPA_FONTDIR"] = str(font_dir)


def _ensure_windows_offscreen_app_font(app: QtWidgets.QApplication) -> None:
    """Load a readable UI font if Qt offscreen started with an empty font DB."""

    global _OFFSCREEN_FONT_LOADED
    platform = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    if platform != "offscreen" or not sys.platform.startswith("win"):
        return
    try:
        if QtGui.QFontDatabase.families():
            if app.font().family() in {"Sans Serif", "MS Shell Dlg 2", ""}:
                app.setFont(QtGui.QFont("Segoe UI", 9))
            return
    except Exception:
        return
    if not _OFFSCREEN_FONT_LOADED:
        font_file = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
        if font_file.exists():
            try:
                _OFFSCREEN_FONT_LOADED = QtGui.QFontDatabase.addApplicationFont(str(font_file)) >= 0
            except Exception:
                _OFFSCREEN_FONT_LOADED = False
    if _OFFSCREEN_FONT_LOADED:
        app.setFont(QtGui.QFont("Segoe UI", 9))


_configure_windows_offscreen_font_dir()


def _install_qt_message_filter() -> None:
    global _QT_MESSAGE_HANDLER_INSTALLED, _QT_MESSAGE_HANDLER
    if _QT_MESSAGE_HANDLER_INSTALLED:
        return

    def _handler(mode, context, message):  # type: ignore[override]
        if "QWindowsWindow::setGeometry" in message:
            return
        if _QT_MESSAGE_HANDLER is not None:
            _QT_MESSAGE_HANDLER(mode, context, message)
            return
        default_handler = getattr(QtCore, "qDefaultMessageHandler", None)
        if callable(default_handler):
            default_handler(mode, context, message)

    _QT_MESSAGE_HANDLER = QtCore.qInstallMessageHandler(_handler)
    _QT_MESSAGE_HANDLER_INSTALLED = True


def ensure_app_theme(app: QtWidgets.QApplication) -> None:
    """Apply the stored theme preference to ``app``."""

    _install_qt_message_filter()
    apply_system_theme(app)
    _ensure_windows_offscreen_app_font(app)


__all__ = ["ensure_app_theme", "apply_system_theme"]
