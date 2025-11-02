from __future__ import annotations

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin
from plotting.maxion_continuous import maxion_gui


class MaxionContinuousPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        try:
            maxion_gui.orig.ProgressDialog = maxion_gui.ProgressDialog
        except Exception:
            pass
        return maxion_gui.SettingsDialog()
