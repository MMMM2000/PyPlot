from __future__ import annotations

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin
from plotting.strain_3d_plot import Strain3DPlotter


class Strain3DPlotPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_widget)

    @staticmethod
    def _create_widget() -> QtWidgets.QWidget:
        return Strain3DPlotter()
