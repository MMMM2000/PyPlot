from __future__ import annotations

import os
import sys
import time

from PyQt6 import QtWidgets

import launcher as launcher_module


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication(sys.argv[:1])
    return app


def _wait_for_registry(window: launcher_module.MasterLauncher, app: QtWidgets.QApplication) -> None:
    for _ in range(40):
        app.processEvents()
        if getattr(window, "_registry_loaded", False):
            return
    raise AssertionError("Launcher registry did not finish loading in time.")


def test_launcher_plotting_list_refreshes_using_last_opened_order(
    monkeypatch,
) -> None:
    app = _ensure_app()
    fake_registry = {
        "loggers": {},
        "plotters": {
            "ZZ Plot A": lambda: None,
            "ZZ Plot B": lambda: None,
        },
        "emulators": {},
    }
    monkeypatch.setattr(launcher_module, "_build_registry", lambda: fake_registry)

    window = launcher_module.MasterLauncher()
    try:
        _wait_for_registry(window, app)
        assert window._sort_modes.get("plotters") == "last_used"  # noqa: SLF001 - test hook

        now = time.time()
        window._settings.setValue("last_used/plotters/ZZ Plot A", now - 100.0)  # noqa: SLF001 - test hook
        window._settings.setValue("last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window._refresh_list("plotters")  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot B"

        # Simulate tool usage while the launcher is hidden, then restore it:
        # _restore_launcher should refresh the visible order from settings.
        window._settings.setValue("last_used/plotters/ZZ Plot A", now + 200.0)  # noqa: SLF001 - test hook
        window._settings.setValue("last_used/plotters/ZZ Plot B", now)  # noqa: SLF001 - test hook
        window.hide()
        window._restore_launcher()  # noqa: SLF001 - test hook
        app.processEvents()
        assert window.plot_list.item(0).text() == "ZZ Plot A"
    finally:
        window.close()
        app.processEvents()
