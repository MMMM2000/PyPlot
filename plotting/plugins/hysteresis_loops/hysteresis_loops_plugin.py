from __future__ import annotations

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin
from plotting.hysteresis_loops import loops_gui


class HysteresisLoopsPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        return loops_gui.SettingsDialog()
