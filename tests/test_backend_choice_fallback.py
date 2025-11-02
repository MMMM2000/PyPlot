from __future__ import annotations

import importlib
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

    original = getattr(utils, "restore_backend_choice", None)
    if original is None:
        pytest.skip("restore_backend_choice helper not available")

    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        owns_app = True

    try:
        monkeypatch.delattr(utils, "restore_backend_choice", raising=True)
        module = importlib.reload(importlib.import_module("plotting.stress_sensitivity.sens_gui"))

        combo = QtWidgets.QComboBox()
        combo.addItems(["Matplotlib", "Origin", "Both"])

        result = module.restore_backend_choice("legacy", combo, "origin")

        assert result == "origin"
        assert combo.currentIndex() == 1
    finally:
        if original is not None:
            setattr(utils, "restore_backend_choice", original)
            importlib.reload(importlib.import_module("plotting.stress_sensitivity.sens_gui"))
        if owns_app and app is not None:
            app.quit()
