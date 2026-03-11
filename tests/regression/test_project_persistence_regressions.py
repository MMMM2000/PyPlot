from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6 import QtGui, QtWidgets

from plotting.pyplot.app import PyPlotWorkbench
from plotting.plugins.vsm_hysteresis import vsm_hysteresis_loops as vsm_module


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def test_vsm_payload_build_does_not_require_bound_json_helper(tmp_path: Path) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    try:
        if hasattr(window, "_json_friendly"):
            delattr(window, "_json_friendly")
        payload = vsm_module.VSMPlotter._build_project_payload(window, base_path=tmp_path)
        assert isinstance(payload, dict)
        assert "measurements" in payload
    finally:
        window.close()
        app.processEvents()


def test_vsm_close_event_falls_back_to_pyplot_window_when_rebound(
    monkeypatch,
) -> None:
    app = _ensure_app()
    window = PyPlotWorkbench(initial_plotter="VSM Hysteresis Loops")
    called = {"fallback": False}

    def _fake_close(_self: object, _event: QtGui.QCloseEvent) -> None:
        called["fallback"] = True

    monkeypatch.setattr(vsm_module.PyPlotWindow, "closeEvent", _fake_close)
    try:
        event = QtGui.QCloseEvent()
        vsm_module.VSMPlotter.closeEvent(window, event)
        assert called["fallback"] is True
    finally:
        window.close()
        app.processEvents()
