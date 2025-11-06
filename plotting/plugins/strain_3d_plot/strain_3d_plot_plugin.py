from __future__ import annotations

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin, register_plugin
from .widget import Strain3DPlotter


@register_plugin("Strain 3D Plot")
class Strain3DPlotPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_widget)

    @staticmethod
    def _create_widget() -> QtWidgets.QWidget:
        return Strain3DPlotter()
