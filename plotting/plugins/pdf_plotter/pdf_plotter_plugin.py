from __future__ import annotations

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin, register_plugin
from . import dialog


@register_plugin("PDF Plotter")
class PdfPlotterPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_dialog)

    @staticmethod
    def _create_dialog() -> QtWidgets.QWidget:
        return dialog.PdfPlotterWindow()
