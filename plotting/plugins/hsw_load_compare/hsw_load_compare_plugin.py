from __future__ import annotations

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin
from plotting.hsw_load_compare import load_compare_gui


class HswLoadComparePlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        try:
            load_compare_gui.orig.ProgressDialog = load_compare_gui.ProgressDialog
        except Exception:
            pass
        return load_compare_gui.SettingsDialog()
