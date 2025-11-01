from __future__ import annotations

from typing import Callable

from PyQt6 import QtCore, QtWidgets


class PyPlotPlugin:
    """Base plugin contract for PyPlot script integrations."""

    requires_imported_data: bool = False

    def __init__(self, host: "PyPlotWorkbench", name: str) -> None:
        self.host = host
        self.name = name
        self._settings_widget: QtWidgets.QWidget | None = None

    # Lifecycle ---------------------------------------------------------
    def activate(self) -> None:
        """Called when the plugin becomes active."""

    def deactivate(self) -> None:
        """Called when the plugin is deselected."""

    # UI helpers --------------------------------------------------------
    def panel_widget(self) -> QtWidgets.QWidget | None:
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QtWidgets.QLabel("Script-specific controls will appear here once implemented.")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        return container

    def settings_widget(self) -> QtWidgets.QWidget:
        if self._settings_widget is None:
            container = QtWidgets.QWidget(self.host)
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            label = QtWidgets.QLabel("No additional settings are exposed for this script yet.")
            label.setWordWrap(True)
            layout.addWidget(label)
            layout.addStretch(1)
            self._settings_widget = container
        return self._settings_widget

    # Host actions ------------------------------------------------------
    def load_data(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "This script does not provide a load handler yet.",
        )

    def generate(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Generation is not implemented for this plotting script yet.",
        )

    def open_matplotlib(self) -> None:
        opener = getattr(self.host, "_open_matplotlib_window", None)
        if callable(opener):
            opener()
            return
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Matplotlib export is not available for this plotting script yet.",
        )

    def save_graph(self) -> None:
        saver = getattr(self.host, "_save_graph_for_current_tab", None)
        if callable(saver):
            saver(parent=self.host)
            return
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Graph saving is not available for this plotting script yet.",
        )

    def normalize(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Normalization is not available for this plotting script yet.",
        )

    def export_txt(self) -> None:
        exporter = getattr(self.host, "_export_txt", None)
        if callable(exporter):
            exporter()
            return
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "TXT export is not available for this plotting script yet.",
        )

    def open_origin(self) -> None:
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Origin export is not available for this plotting script yet.",
        )


class ExternalPlotterPlugin(PyPlotPlugin):
    """Adapter that launches legacy standalone plotters from within PyPlot."""

    def __init__(
        self,
        host: "PyPlotWorkbench",
        name: str,
        launcher: Callable[[], QtWidgets.QWidget | None],
    ) -> None:
        super().__init__(host, name)
        self._launcher = launcher
        self._panel: QtWidgets.QWidget | None = None
        self._window: QtWidgets.QWidget | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        if self._panel is not None:
            return self._panel
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = QtWidgets.QLabel(
            f"{self.name} opens in its dedicated window. Click Launch to continue."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        launch_btn = QtWidgets.QPushButton(f"Launch {self.name}")
        launch_btn.clicked.connect(self._launch)
        layout.addWidget(launch_btn)
        layout.addStretch(1)
        self._panel = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget:  # type: ignore[override]
        widget = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QtWidgets.QLabel("No additional settings are available."))
        layout.addStretch(1)
        return widget

    def _launch(self) -> None:
        try:
            window = self._launcher()
        except Exception as exc:  # pragma: no cover - defensive
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"Failed to launch legacy plotter:\n{exc}",
            )
            return
        if isinstance(window, QtWidgets.QWidget):
            window.show()
            self._window = window

    def load_data(self) -> None:  # type: ignore[override]
        self._launch()

    def generate(self) -> None:  # type: ignore[override]
        self._launch()

    def open_matplotlib(self) -> None:  # type: ignore[override]
        self._launch()

    def update_ui(self) -> None:
        if hasattr(self.host, "load_data_button"):
            self.host.load_data_button.setEnabled(False)
        if hasattr(self.host, "plot_button"):
            self.host.plot_button.setEnabled(False)
        if hasattr(self.host, "save_graph_button"):
            self.host.save_graph_button.setEnabled(False)
        if hasattr(self.host, "normalize_button"):
            self.host.normalize_button.setEnabled(False)
        if hasattr(self.host, "export_button"):
            self.host.export_button.setEnabled(False)
        if hasattr(self.host, "open_origin_button"):
            self.host.open_origin_button.setEnabled(False)
        if hasattr(self.host, "popout_button"):
            self.host.popout_button.setEnabled(False)


class EmbeddedWidgetPlugin(PyPlotPlugin):
    """Embed a legacy dialog or widget directly inside the PyPlot workbench."""

    def __init__(
        self,
        host: "PyPlotWorkbench",
        name: str,
        widget_factory: Callable[[], QtWidgets.QWidget | None],
    ) -> None:
        super().__init__(host, name)
        self._widget_factory = widget_factory
        self._widget: QtWidgets.QWidget | None = None
        self._panel: QtWidgets.QWidget | None = None

    def activate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)
        self._ensure_widget()
        self.update_ui()

    def deactivate(self) -> None:  # type: ignore[override]
        self.host._set_data_sources_visible(False)
        if self._widget is not None:
            try:
                self._widget.hide()
            except Exception:
                pass

    def _ensure_widget(self) -> QtWidgets.QWidget:
        if self._widget is None:
            widget = self._widget_factory()
            if widget is None:
                widget = QtWidgets.QWidget(self.host)
            self._widget = widget
        return self._widget

    def panel_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        container = QtWidgets.QWidget(self.host)
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        widget = self._ensure_widget()
        widget.setParent(container)
        widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        if isinstance(widget, QtWidgets.QDialog):
            widget.setModal(False)
            try:
                widget.setSizeGripEnabled(False)
            except Exception:
                pass
        try:
            widget.setWindowFlag(QtCore.Qt.WindowType.Dialog, False)
            widget.setWindowFlag(QtCore.Qt.WindowType.Window, False)
        except Exception:
            pass
        widget.show()
        layout.addWidget(widget)
        self._panel = container
        return container

    def settings_widget(self) -> QtWidgets.QWidget | None:  # type: ignore[override]
        return None

    def update_ui(self) -> None:  # type: ignore[override]
        for attr in (
            "load_data_button",
            "plot_button",
            "save_graph_button",
            "normalize_button",
            "export_button",
            "open_origin_button",
            "popout_button",
        ):
            widget = getattr(self.host, attr, None)
            if isinstance(widget, QtWidgets.QAbstractButton):
                widget.setEnabled(False)
                if attr == "plot_button":
                    widget.setText("Generate")


__all__ = [
    "PyPlotPlugin",
    "ExternalPlotterPlugin",
    "EmbeddedWidgetPlugin",
]
