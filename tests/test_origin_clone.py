from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6 import QtWidgets
except Exception as exc:  # pragma: no cover - headless CI fallback
    pytest.skip(f"PyQt6 unavailable: {exc}", allow_module_level=True)

from origin_clone import OriginCloneWindow, main as origin_main


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def test_origin_clone_main_creates_window() -> None:
    _ensure_app()
    window = origin_main()
    try:
        assert isinstance(window, OriginCloneWindow)
    finally:
        window.close()
        window.deleteLater()


def test_origin_clone_workbook_registration() -> None:
    app = _ensure_app()
    window = OriginCloneWindow()
    try:
        assert window.list_workbooks() == []
        window.create_empty_workbook()
        app.processEvents()
        assert len(window.list_workbooks()) == 1
    finally:
        window.close()
        window.deleteLater()
