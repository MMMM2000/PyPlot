"""Shared theming helpers for PyPlot."""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from .toolkit import apply_system_theme

_QT_MESSAGE_HANDLER_INSTALLED = False
_QT_MESSAGE_HANDLER = None


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


__all__ = ["ensure_app_theme", "apply_system_theme"]
