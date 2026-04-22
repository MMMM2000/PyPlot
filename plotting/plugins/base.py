from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

from PyQt6 import QtCore, QtGui, QtWidgets


class PyPlotPlugin:
    """Base plugin contract for PyPlot script integrations."""

    requires_imported_data: bool = False
    exposes_load_data: bool = True
    auto_load_on_import: bool = False
    # Default to shared plot->workbook registration; plug-ins with custom
    # workbook lifecycles can opt out by setting this to False.
    uses_shared_plot_workbooks: bool = True

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
        opener = getattr(self.host, "_open_origin_shared", None)
        if callable(opener):
            opener()
            return
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Origin export is not available for this plotting script yet.",
        )

    def export_origin_workbooks(self) -> None:
        exporter = getattr(self.host, "_export_workbooks_to_origin", None)
        if callable(exporter):
            exporter()
            return
        QtWidgets.QMessageBox.information(
            self.host,
            self.name,
            "Workbook export is not available for this plotting script yet.",
        )

    def update_ui(self) -> None:
        """Optional hook for plugins to refresh toolbar/button state."""
        # Default implementation keeps legacy plugins working even if PyPlot
        # asks them to refresh before they override the method.
        return

    def supports_graph_point_removal(self) -> bool:
        """Return True when the plugin can persist point removals from graphs."""

        return False

    def remove_graph_points(
        self,
        *,
        descriptor: Any,
        point_refs: Iterable[Any],
    ) -> int:
        """Persist removals for selected graph points and return deleted count."""

        _ = (descriptor, point_refs)
        return 0

    # Project persistence ------------------------------------------------
    def serialize_project_state(self, *, base_path: Path | None) -> Dict[str, Any] | None:
        """Return plugin-specific project state to persist, or ``None``."""

        _ = base_path
        return None

    def restore_project_state(self, state: Dict[str, Any], *, project_dir: Path) -> None:
        """Restore plugin-specific state from a previously saved project."""

        _ = (state, project_dir)
        return

    def plot_action_label(self) -> str:
        """Text shown on the shared Plot button when this plugin is active."""

        return f"Plot {self.name}".strip()

    def graph_option_defaults(self) -> Dict[str, Any] | None:
        """Optional plugin-specific graph option defaults.

        Returned values are merged on top of global defaults and can still be
        overridden by explicit plugin overrides from the Graph options dialog.
        """

        return None

    def _log(self, message: str, *, level: str = "info") -> None:
        """Log helper that prefers the host console when available."""

        append = getattr(self.host, "_append_log", None)
        if callable(append):
            try:
                append(message, level=level)
                return
            except Exception:
                pass
        log_level = logging.ERROR if level == "error" else logging.INFO
        logger = logging.getLogger(f"PyPlot.{self.name.replace(' ', '_')}")
        logger.log(log_level, message)

    # Folder history ---------------------------------------------------
    def preferred_import_directory(self) -> Path:
        """Return the starting folder for file pickers scoped to this plug-in."""

        start = None
        if hasattr(self.host, "_dialog_start_directory"):
            try:
                start = self.host._dialog_start_directory()
            except Exception:
                start = None
        if isinstance(start, Path):
            return start
        return Path.home()

    def preferred_export_directory(self, *fallbacks: Path | None) -> Path:
        """Return the starting folder for exports scoped to this plug-in."""

        getter = getattr(self.host, "_preferred_export_directory", None)
        if callable(getter):
            try:
                return getter(self.name, *fallbacks)
            except Exception:
                pass
        for candidate in fallbacks:
            if isinstance(candidate, Path) and candidate.exists():
                return candidate
        return Path.home()

    def remember_export_directory(self, path: Path | None) -> None:
        """Persist the last export folder for this plug-in."""

        if not isinstance(path, Path):
            return
        helper = getattr(self.host, "_remember_plugin_export_dir", None)
        if callable(helper):
            try:
                helper(self.name, path)
                return
            except Exception:
                pass
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        self._log(f"Saved export folder: {resolved}")

    def _host_has_data_selection(self) -> bool:
        """Return True when the host already has imported data or file selections."""

        selected_paths = getattr(self.host, "_selected_paths", None)
        if callable(selected_paths):
            try:
                if selected_paths():
                    return True
            except Exception:
                pass
        entries = getattr(self.host, "_selected_path_entries", None)
        if isinstance(entries, list) and entries:
            return True
        has_imports = getattr(self.host, "_has_imported_data", None)
        if callable(has_imports):
            try:
                if has_imports():
                    return True
            except Exception:
                return False
        return False

    # Shared plugin helpers -------------------------------------------
    def _set_host_action_enabled(self, attr_name: str, enabled: bool) -> None:
        control = getattr(self.host, attr_name, None)
        if isinstance(control, (QtWidgets.QWidget, QtGui.QAction)):
            try:
                control.setEnabled(bool(enabled))
            except Exception:
                pass

    def apply_shared_action_state(
        self,
        *,
        can_plot: bool | None = None,
        can_save_graph: bool | None = None,
        can_normalize: bool | None = None,
        can_export_txt: bool | None = None,
        can_open_origin: bool | None = None,
        can_export_workbooks: bool | None = None,
        can_popout: bool | None = None,
        update_project_actions: bool = True,
    ) -> None:
        if can_plot is not None:
            self._set_host_action_enabled("plot_button", can_plot)
        if can_save_graph is not None:
            self._set_host_action_enabled("save_graph_button", can_save_graph)
        if can_normalize is not None:
            self._set_host_action_enabled("normalize_button", can_normalize)
        if can_export_txt is not None:
            self._set_host_action_enabled("export_button", can_export_txt)
        if can_open_origin is not None:
            self._set_host_action_enabled("open_origin_button", can_open_origin)
        if can_export_workbooks is not None:
            self._set_host_action_enabled("export_origin_button", can_export_workbooks)
        if can_popout is not None:
            self._set_host_action_enabled("popout_button", can_popout)
        if update_project_actions:
            updater = getattr(self.host, "_update_project_actions", None)
            if callable(updater):
                try:
                    updater()
                except Exception:
                    pass

    def clear_plot_tabs(self, tabs: list[QtWidgets.QWidget]) -> None:
        if not tabs:
            return
        clear = getattr(self.host, "_clear_tab_list", None)
        if callable(clear):
            clear(tabs)
        else:
            for tab in list(tabs):
                index = self.host.tab_widget.indexOf(tab)
                if index >= 0:
                    self.host.tab_widget.removeTab(index)
        tabs.clear()
        rebuild = getattr(self.host, "_rebuild_object_manager_for_tab", None)
        if callable(rebuild):
            try:
                rebuild(self.host.tab_widget.currentWidget())
            except Exception:
                pass

    def run_origin_export(
        self,
        *,
        ready: bool,
        missing_message: str,
        task: Callable[[], None],
        success_log: str | None = None,
        failure_message: str = "Failed to export to Origin",
        failure_log_prefix: str = "Origin export failed",
    ) -> bool:
        if not ready:
            QtWidgets.QMessageBox.information(
                self.host,
                self.name,
                missing_message,
            )
            return False
        try:
            task()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self.host,
                self.name,
                f"{failure_message}:\n{exc}",
            )
            self._log(f"{failure_log_prefix}: {exc}", level="error")
            return False
        if isinstance(success_log, str) and success_log.strip():
            self._log(success_log)
        return True


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
        self.apply_shared_action_state(
            can_plot=False,
            can_save_graph=False,
            can_normalize=False,
            can_export_txt=False,
            can_open_origin=False,
            can_popout=False,
        )


class EmbeddedWidgetPlugin(PyPlotPlugin):
    """Embed a legacy dialog or widget directly inside the PyPlot workbench."""

    exposes_load_data = False

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
        self.apply_shared_action_state(
            can_plot=False,
            can_save_graph=False,
            can_normalize=False,
            can_export_txt=False,
            can_open_origin=False,
            can_popout=False,
            update_project_actions=False,
        )
        widget = getattr(self.host, "plot_button", None)
        if hasattr(widget, "setText"):
            widget.setText("Plot graphs")


__all__ = [
    "PyPlotPlugin",
    "ExternalPlotterPlugin",
    "EmbeddedWidgetPlugin",
]


_PLUGIN_REGISTRY: Dict[str, type[PyPlotPlugin]] = {}


def register_plugin(name: str) -> Callable[[type[PyPlotPlugin]], type[PyPlotPlugin]]:
    """Decorator that registers ``PyPlotPlugin`` subclasses by display name."""

    def decorator(cls: type[PyPlotPlugin]) -> type[PyPlotPlugin]:
        if not issubclass(cls, PyPlotPlugin):
            raise TypeError("Registered class must inherit from PyPlotPlugin")
        _PLUGIN_REGISTRY[name] = cls
        return cls

    return decorator


def get_plugin_registry() -> Dict[str, type[PyPlotPlugin]]:
    """Return a copy of the registered plugin mapping sorted by name."""

    return dict(sorted(_PLUGIN_REGISTRY.items(), key=lambda item: item[0].lower()))


def iter_registered_plugins() -> Iterable[tuple[str, type[PyPlotPlugin]]]:
    """Iterate over registered plugin entries in sorted order."""

    for name, cls in get_plugin_registry().items():
        yield name, cls


def clear_plugin_registry() -> None:
    """Reset the registry – primarily useful for tests."""

    _PLUGIN_REGISTRY.clear()


__all__.extend(
    [
        "register_plugin",
        "get_plugin_registry",
        "iter_registered_plugins",
        "clear_plugin_registry",
    ]
)
