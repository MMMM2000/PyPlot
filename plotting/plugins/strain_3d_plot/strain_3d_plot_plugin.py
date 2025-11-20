from __future__ import annotations

from PyQt6 import QtWidgets

from plotting.plugins.base import EmbeddedWidgetPlugin, register_plugin
from plotting.pyplot import window as window_module
from .widget import Strain3DPlotter


@register_plugin("Strain 3D Plot")
class Strain3DPlotPlugin(EmbeddedWidgetPlugin):
    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        super().__init__(host, name, self._create_widget)
        self._settings_panel: QtWidgets.QWidget | None = None

    @staticmethod
    def _create_widget() -> QtWidgets.QWidget:
        return Strain3DPlotter()

    def _focus_widget(self) -> None:
        widget = self._ensure_widget()
        try:
            widget.setFocus()
        except Exception:
            pass

    def _trigger_input_browse(self) -> None:
        widget = self._ensure_widget()
        chooser = getattr(widget, "input_button", None)
        if isinstance(chooser, QtWidgets.QAbstractButton):
            chooser.click()

    def settings_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._settings_panel is not None:
            return self._settings_panel

        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        overview_section, overview_layout = window_module.create_toolbar_section(
            "Usage",
            parent=container,
        )
        overview_label = QtWidgets.QLabel(
            "Use the embedded Strain 3D Plot controls to choose worksheets or "
            "database exports and then generate Matplotlib or Origin scatter plots."
        )
        overview_label.setWordWrap(True)
        overview_layout.addWidget(overview_label)
        layout.addWidget(overview_section)

        shortcuts_section, shortcuts_layout = window_module.create_toolbar_section(
            "Shortcuts",
            parent=container,
        )
        focus_button = QtWidgets.QPushButton("Focus embedded plotter")
        focus_button.clicked.connect(self._focus_widget)
        shortcuts_layout.addWidget(focus_button)
        browse_button = QtWidgets.QPushButton("Choose worksheet…")
        browse_button.clicked.connect(self._trigger_input_browse)
        shortcuts_layout.addWidget(browse_button)
        shortcuts_layout.addStretch(1)
        layout.addWidget(shortcuts_section)

        layout.addStretch(1)
        self._settings_panel = container
        return container
