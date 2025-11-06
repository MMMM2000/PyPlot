from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_restore_backend_choice_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PyQt6", reason="PyQt6 is required for backend tests")
    try:
        from PyQt6 import QtWidgets
    except ImportError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PyQt6 widgets unavailable: {exc}")

    from plotting.shared import toolkit as utils

    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        owns_app = True

    class _DummySettings:
        def value(self, key: str, default: str, type=str) -> str:  # noqa: A003
            return "invalid"

    try:
        monkeypatch.setattr(utils, "_settings", lambda: _DummySettings())

        combo = QtWidgets.QComboBox()
        combo.addItems(["Matplotlib", "Origin", "Both"])

        result = utils.restore_backend_choice("legacy", combo, "origin")

        assert result == "origin"
        assert combo.currentIndex() == 1
    finally:
        if owns_app and app is not None:
            app.quit()
