from __future__ import annotations

import argparse
import sys
import os
import time
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING, Any, Callable, Dict, Tuple, cast, Protocol

from PyQt6 import QtWidgets, QtGui, QtCore


LauncherFactory = Callable[..., QtWidgets.QWidget | None]

if TYPE_CHECKING:
    from plotting.shared import common as _common_module


class _DeveloperOptionsProtocol(Protocol):
    experiments_visibility_changed: QtCore.pyqtBoundSignal

    def show_experiments(self) -> bool:
        ...

    def set_show_experiments(self, enabled: bool) -> None:
        ...


def _lazy(module: str, attr: str = "main") -> LauncherFactory:
    def factory(*args: Any, **kwargs: Any) -> QtWidgets.QWidget | None:
        module_obj = import_module(module)
        target: Any = module_obj
        for segment in attr.split("."):
            target = getattr(target, segment)
        if not callable(target):
            raise TypeError(f"{module}.{attr} is not callable")
        callable_target = cast(LauncherFactory, target)
        return callable_target(*args, **kwargs)

    return factory


LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_install_standard_menu() -> Callable[..., QtWidgets.QMenuBar]:
    from plotting.shared.utils import install_standard_menu

    return install_standard_menu


@lru_cache(maxsize=1)
def _load_developer_options() -> Callable[[], "_DeveloperOptionsProtocol"]:
    module = import_module("plotting.shared.developer")
    return cast(Callable[[], "_DeveloperOptionsProtocol"], getattr(module, "developer_options"))


def _install_launcher_menu(*args: Any, **kwargs: Any) -> QtWidgets.QMenuBar:
    install = _load_install_standard_menu()
    return install(*args, **kwargs)


def _reset_outlier_flags() -> None:
    try:
        common_module = cast(
            "_common_module", import_module("plotting.shared.common")
        )
    except Exception:
        LOGGER.debug("Unable to load plotting.shared.common", exc_info=True)
        return
    common_module.CHECK_OUTLIERS = False
    common_module.AUTO_REMOVE_OUTLIERS = False


def _schedule_theme_application(app: QtWidgets.QApplication) -> None:
    def _apply_theme() -> None:
        try:
            from plotting.shared.theme import ensure_app_theme
        except Exception:
            LOGGER.debug("Unable to import plotting.shared.theme", exc_info=True)
            return
        try:
            ensure_app_theme(app)
        except Exception:
            LOGGER.warning("Failed to apply app theme", exc_info=True)

    QtCore.QTimer.singleShot(0, _apply_theme)


def _crash_log_path() -> Path:
    return Path(__file__).resolve().parent / "logs" / "crash_log.txt"


def _append_crash_log(message: str) -> None:
    try:
        from plotting.shared.logfiles import append_text_with_rotation
    except Exception:
        return
    try:
        append_text_with_rotation(
            _crash_log_path(),
            message,
            max_bytes=1_000_000,
            backup_count=5,
        )
    except Exception:
        pass


def _install_crash_log_hook() -> None:
    previous_hook = sys.excepthook

    def _hook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous_hook(exc_type, exc_value, exc_tb)
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _append_crash_log(f"[{timestamp}] Unhandled exception\n{trace}\n")
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


@lru_cache(maxsize=1)
def _load_pyplot_metadata() -> Tuple[LauncherFactory, Tuple[str, ...]]:
    from plotting.pyplot.app import main as pyplot_main
    from plotting.plugins import builtin_plugin_registry

    plugin_names = tuple(sorted(builtin_plugin_registry()))
    return cast(LauncherFactory, pyplot_main), plugin_names


def _plotter_registry() -> Dict[str, LauncherFactory]:
    pyplot_main, plugin_names = _load_pyplot_metadata()
    registry: Dict[str, LauncherFactory] = {
        "PyPlot": lambda: pyplot_main(initial_plotter=None)
    }
    for name in plugin_names:
        registry[name] = (
            lambda plotter_name=name: pyplot_main(initial_plotter=plotter_name)
        )
    return registry


@lru_cache(maxsize=1)
def _load_experiments_registry() -> Dict[str, LauncherFactory]:
    try:
        from experiments import EXPERIMENTS as experiments_map
    except Exception as exc:
        LOGGER.warning("Failed to load experiments module", exc_info=exc)
        return {}
    return dict(experiments_map)


def _build_registry() -> dict[str, Dict[str, LauncherFactory]]:
    registry: dict[str, Dict[str, LauncherFactory]] = {
        "loggers": dict(LOGGERS),
        "plotters": _plotter_registry(),
        "emulators": dict(EMULATORS),
    }
    if BUILDERS:
        registry["builders"] = dict(BUILDERS)
    experiments = _load_experiments_registry()
    if experiments:
        registry["experiments"] = experiments
    return registry


def launch_pyplot(initial: str | None = None) -> QtWidgets.QWidget | None:
    """Open the base plotter workbench, optionally selecting a script."""

    pyplot_main, _ = _load_pyplot_metadata()
    return pyplot_main(initial_plotter=initial)


def _create_launcher_icon() -> QtGui.QIcon:
    """Return the shared launcher icon, generating it on first use."""

    cached: QtGui.QIcon | None = getattr(_create_launcher_icon, "_cache", None)
    if isinstance(cached, QtGui.QIcon):
        return cached
    size = 256
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    background = QtGui.QColor("#1f2937")
    painter.setBrush(background)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    rect = pixmap.rect().adjusted(12, 12, -12, -12)
    radius = size * 0.18
    painter.drawRoundedRect(rect, radius, radius)
    painter.setPen(QtGui.QPen(QtGui.QColor("#f9fafb")))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(88)
    painter.setFont(font)
    painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "Py")
    painter.end()
    icon = QtGui.QIcon(pixmap)
    setattr(_create_launcher_icon, "_cache", icon)
    return icon


def _parse_launcher_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--visual-check",
        action="store_true",
        help="Run automated visual verification flow instead of opening the launcher UI.",
    )
    parser.add_argument(
        "--visual-plugin",
        default="shape-memory",
        help="Plugin visual-check target. Currently supported: shape-memory.",
    )
    parser.add_argument(
        "--visual-input",
        action="append",
        default=[],
        help="Input file path for visual-check mode. Can be provided multiple times.",
    )
    parser.add_argument(
        "--visual-layout",
        choices=("dual", "separate"),
        default="dual",
        help="Shape-memory graph layout for visual-check mode.",
    )
    parser.add_argument(
        "--visual-output-dir",
        default=str(Path("logs") / "visual_checks"),
        help="Directory where visual-check artifacts will be saved.",
    )
    parser.add_argument(
        "--visual-origin",
        dest="visual_origin",
        action="store_true",
        help="Enable Origin graph export capture in visual-check mode (default).",
    )
    parser.add_argument(
        "--no-visual-origin",
        dest="visual_origin",
        action="store_false",
        help="Disable Origin capture during visual-check mode.",
    )
    parser.add_argument(
        "--visual-show-window",
        action="store_true",
        help="Keep UI visible while visual-check runs.",
    )
    parser.set_defaults(visual_origin=True)
    args, qt_args = parser.parse_known_args(argv)
    return args, qt_args


def _run_visual_check(args: argparse.Namespace) -> int:
    plugin_token = str(getattr(args, "visual_plugin", "shape-memory")).strip().lower()
    supported_tokens = {
        "shape-memory",
        "shape_memory",
        "shape-memory-stress-strain",
        "shape_memory_stress_strain",
        "shape memory stress/strain",
    }
    if plugin_token not in supported_tokens:
        print(
            f"Unsupported --visual-plugin '{plugin_token}'. "
            "Only shape-memory visual-check is currently implemented."
        )
        return 2

    from plotting.pyplot.visual_check import run_shape_memory_visual_check

    output_dir = Path(str(getattr(args, "visual_output_dir", "logs/visual_checks"))).expanduser()
    raw_inputs = getattr(args, "visual_input", []) or []
    input_paths = [Path(str(entry)).expanduser() for entry in raw_inputs]
    include_origin = bool(getattr(args, "visual_origin", True))
    layout_mode = str(getattr(args, "visual_layout", "dual")).strip().lower()
    show_window = bool(getattr(args, "visual_show_window", False))

    result = run_shape_memory_visual_check(
        output_dir=output_dir,
        input_paths=input_paths or None,
        layout_mode=layout_mode,
        include_origin=include_origin,
        show_window=show_window,
    )
    print(f"[visual-check] output_dir={result.output_dir}")
    if result.summary_json is not None:
        print(f"[visual-check] summary={result.summary_json}")
    if result.window_image is not None:
        print(f"[visual-check] pyplot_window_png={result.window_image}")
    if result.tab_widget_image is not None:
        print(f"[visual-check] pyplot_tab_widget_png={result.tab_widget_image}")
    print(f"[visual-check] matplotlib_images={len(result.matplotlib_images)}")
    print(f"[visual-check] matplotlib_canvas_images={len(result.matplotlib_canvas_images)}")
    print(f"[visual-check] subwindow_images={len(result.subwindow_images)}")
    print(f"[visual-check] origin_images={len(result.origin_images)}")
    for warning in result.warnings:
        print(f"[visual-check][warn] {warning}")
    for error in result.errors:
        print(f"[visual-check][error] {error}")
    return 1 if result.errors else 0

LOGGERS: Dict[str, LauncherFactory] = {
    "Serial Data Logger": _lazy("data_logging.data_logger", "main"),
    "Current Annealing Logger": _lazy(
        "data_logging.current_annealing_logger", "main"
    ),
    "Manual Stress/Strain Logger": _lazy(
        "data_logging.manual_stress_strain_logger", "main"
    ),
}

EMULATORS: Dict[str, LauncherFactory] = {
    "Universal Serial Emulator": _lazy(
        "emulators.virtual_serial_emulator_gui", "main"
    ),
}

BUILDERS: Dict[str, LauncherFactory] = {
    "Microwire Data Builder": _lazy("microwire_data_builder", "main"),
}


class MasterLauncher(QtWidgets.QWidget):
    ready = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PyPlot Launcher")
        self.setWindowIcon(_create_launcher_icon())
        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Ensure window bookkeeping exists even if later setup fails so the
        # destroyed callbacks can run safely.
        self._open_windows: list[QtWidgets.QWidget] = []

        self._settings = QtCore.QSettings("MicrowireData", "Launcher")
        dev_opts_factory = _load_developer_options()
        self.dev_opts = dev_opts_factory()
        self._closing = False
        self._registry_loaded = False
        placeholder_plotters: Dict[str, LauncherFactory] = {
            "PyPlot": lambda: launch_pyplot(initial=None)
        }
        self._registry: dict[str, Dict[str, LauncherFactory]] = {
            "loggers": dict(LOGGERS),
            "plotters": placeholder_plotters,
            "emulators": dict(EMULATORS),
        }
        if BUILDERS:
            self._registry["builders"] = dict(BUILDERS)

        try:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_QuitOnClose, False)
        except Exception:
            pass

        app = QtWidgets.QApplication.instance()
        if isinstance(app, QtWidgets.QApplication):
            try:
                app.setQuitOnLastWindowClosed(False)
            except Exception:
                pass
            try:
                app.lastWindowClosed.connect(self._restore_launcher)
            except Exception:
                pass
            try:
                app.installEventFilter(self)
            except Exception:
                pass

        self.search_bar = QtWidgets.QLineEdit(self)
        self.search_bar.setPlaceholderText("Search tools...")
        try:
            self.search_bar.setClearButtonEnabled(True)
        except Exception:
            pass
        self.tabs = QtWidgets.QTabWidget()
        self.log_tab = QtWidgets.QWidget()
        self.plot_tab = QtWidgets.QWidget()
        self.emu_tab = QtWidgets.QWidget()
        self.builder_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.log_tab, "Loggers")
        self.tabs.addTab(self.plot_tab, "Plotting")
        self.tabs.addTab(self.emu_tab, "Emulators")
        if self._registry.get("builders"):
            self.tabs.addTab(self.builder_tab, "Builders")
        self.exp_tab = QtWidgets.QWidget()
        self._experiments_index: int | None = None

        self.log_list = QtWidgets.QListWidget()
        log_layout = QtWidgets.QVBoxLayout(self.log_tab)
        log_layout.addWidget(self.log_list)

        self.plot_list = QtWidgets.QListWidget()
        plot_layout = QtWidgets.QVBoxLayout(self.plot_tab)
        plot_layout.addWidget(self.plot_list)

        self.emu_list = QtWidgets.QListWidget()
        emu_layout = QtWidgets.QVBoxLayout(self.emu_tab)
        emu_layout.addWidget(self.emu_list)

        self.builder_list = QtWidgets.QListWidget()
        builder_layout = QtWidgets.QVBoxLayout(self.builder_tab)
        builder_layout.addWidget(self.builder_list)

        self.exp_list = QtWidgets.QListWidget()
        exp_layout = QtWidgets.QVBoxLayout(self.exp_tab)
        exp_layout.addWidget(self.exp_list)

        self._update_category_labels()

        self._list_widgets = {
            "loggers": self.log_list,
            "plotters": self.plot_list,
            "emulators": self.emu_list,
        }
        if self._registry.get("builders"):
            self._list_widgets["builders"] = self.builder_list
        if "experiments" in self._registry:
            self._list_widgets["experiments"] = self.exp_list

        self._sort_modes: dict[str, str] = {}
        for category in self._list_widgets:
            stored = self._settings.value(f"sort/{category}", "last_used")
            if not isinstance(stored, str) or stored not in {"last_used", "name_asc", "name_desc"}:
                stored = "last_used"
            self._sort_modes[category] = stored
        self._last_order_counter = self._load_last_order_counter()
        # Keep plotting tools in "last opened" order regardless of prior sort
        # settings so recent workflows stay at the top.
        self._sort_modes["plotters"] = "last_used"
        try:
            self._settings.setValue("sort/plotters", "last_used")
        except Exception:
            pass
        self._sort_groups: dict[str, QtGui.QActionGroup] = {}

        self.main_layout.addWidget(self.search_bar)
        self.main_layout.addWidget(self.tabs)

        self._set_lists_loading()
        QtCore.QTimer.singleShot(0, self._load_registry_async)
        self.dev_opts.experiments_visibility_changed.connect(self._sync_experiments_tab)
        self.search_bar.textChanged.connect(self._apply_search_filter)
        self.tabs.currentChanged.connect(self._handle_tab_changed)

        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)
        self.run_button.setEnabled(False)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.run_button)

        self.main_layout.addLayout(button_row)

        menu_bar = _install_launcher_menu(
            self,
            help_topic="launcher",
            close_window=self._close_launcher,
        )
        sort_menu = menu_bar.addMenu("&Sort")
        if sort_menu is None:
            sort_menu = QtWidgets.QMenu("&Sort", self)
            menu_bar.addMenu(sort_menu)
        self._sort_menu = sort_menu
        self._install_sort_menu(sort_menu)

    def _close_launcher(self) -> None:
        """Close hook that satisfies :func:`install_standard_menu`."""

        # ``QWidget.close`` returns ``bool`` and Pylance/Pyright expect the menu
        # callback to return ``None``.  We call the underlying method but
        # intentionally drop the return value to keep the type contract tidy.
        self.close()

    def _set_lists_loading(self) -> None:
        for list_widget in self._list_widgets.values():
            list_widget.clear()
            list_widget.setEnabled(False)
            placeholder = QtWidgets.QListWidgetItem("Loading...")
            placeholder.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            list_widget.addItem(placeholder)

    def _update_category_labels(self) -> None:
        labels: dict[str, str] = {
            "loggers": "Loggers",
            "plotters": "Plotting",
            "emulators": "Emulators",
        }
        if self._registry.get("builders"):
            labels["builders"] = "Builders"
        experiments = self._registry.get("experiments")
        if experiments:
            labels["experiments"] = "Experiments"
        self._category_labels = labels

    def _load_registry_async(self) -> None:
        try:
            registry = _build_registry()
        except Exception as exc:  # pragma: no cover - unexpected import failure
            LOGGER.exception("Failed to build launcher registry", exc_info=exc)
            QtWidgets.QMessageBox.critical(
                self,
                "Launcher error",
                f"Failed to load tools:\n{exc}",
            )
            registry = None
        else:
            self._registry = registry
            if "experiments" in registry:
                self._list_widgets["experiments"] = self.exp_list
            self._update_category_labels()
            if hasattr(self, "_sort_menu"):
                self._sort_menu.clear()
                self._sort_groups.clear()
                self._install_sort_menu(self._sort_menu)
            for category in registry:
                self._sort_modes.setdefault(category, "last_used")
        finally:
            self._registry_loaded = True
            self.run_button.setEnabled(True)
            self._apply_search_filter(self.search_bar.text())
            self._sync_experiments_tab(self.dev_opts.show_experiments())
            self.ready.emit()

    def _restore_launcher(self) -> None:
        if self._closing:
            return
        if self._registry_loaded:
            self._refresh_all_lists()
        if not self.isVisible():
            self.show()
            try:
                self.raise_()
                self.activateWindow()
            except Exception:
                pass

    def changeEvent(self, event: QtCore.QEvent) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.ActivationChange:
            if self.isActiveWindow() and self._registry_loaded:
                self._refresh_all_lists()

    def _register_window(self, widget: QtWidgets.QWidget) -> None:
        """Track ``widget`` so closing the launcher can warn appropriately."""

        if widget in self._open_windows:
            return

        self._open_windows.append(widget)

        try:
            widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_QuitOnClose, False)
        except Exception:
            pass

        def _remove(_: object = None, w: QtWidgets.QWidget = widget) -> None:
            windows = getattr(self, "_open_windows", None)
            if windows is None:
                return
            try:
                windows.remove(w)
            except ValueError:
                pass

        widget.destroyed.connect(_remove)

    def _sync_experiments_tab(self, enabled: bool) -> None:
        has_items = self.exp_list.count() > 0
        index = self.tabs.indexOf(self.exp_tab)
        if enabled and has_items:
            if index == -1:
                self._experiments_index = self.tabs.addTab(
                    self.exp_tab, "Experiments"
                )
        else:
            if index != -1:
                self.tabs.removeTab(index)
            self._experiments_index = None

    def _install_sort_menu(self, parent_menu: QtWidgets.QMenu) -> None:
        for category, label in self._category_labels.items():
            if category not in self._list_widgets:
                continue
            if not self._registry.get(category):
                continue
            submenu = parent_menu.addMenu(label)
            if submenu is None:
                submenu = QtWidgets.QMenu(label, self)
                parent_menu.addMenu(submenu)
            if submenu is None:
                continue
            group = QtGui.QActionGroup(self)
            group.setExclusive(True)
            for mode, text in (
                ("last_used", "Last Used (Most Recent)"),
                ("name_asc", "Name (A-Z)"),
                ("name_desc", "Name (Z-A)"),
            ):
                action = submenu.addAction(text)
                if action is None:
                    continue
                action.setCheckable(True)
                action.setData((category, mode))
                if self._sort_modes.get(category, "last_used") == mode:
                    action.setChecked(True)
                group.addAction(action)
            group.triggered.connect(self._handle_sort_trigger)
            self._sort_groups[category] = group

    def _apply_search_filter(self, _: str) -> None:
        self._refresh_all_lists()

    def _refresh_all_lists(self) -> None:
        for category, list_widget in self._list_widgets.items():
            current_item = list_widget.currentItem()
            selected = current_item.text() if current_item is not None else None
            self._refresh_list(category, select_name=selected)

    def _refresh_list(self, category: str, select_name: str | None = None) -> None:
        list_widget = self._list_widgets.get(category)
        if list_widget is None:
            return
        names = self._sorted_names(category)
        search_text = self.search_bar.text().strip().casefold()
        list_widget.blockSignals(True)
        list_widget.clear()
        for name in names:
            if search_text and search_text not in name.casefold():
                continue
            list_widget.addItem(name)
        list_widget.blockSignals(False)
        list_widget.setEnabled(self._registry_loaded)
        if select_name:
            matches = list_widget.findItems(select_name, QtCore.Qt.MatchFlag.MatchExactly)
            if matches:
                list_widget.setCurrentItem(matches[0])
        if list_widget.currentRow() == -1 and list_widget.count():
            list_widget.setCurrentRow(0)

    def _current_list_widget(self) -> QtWidgets.QListWidget | None:
        current = self.tabs.currentWidget()
        if current is self.log_tab:
            return self.log_list
        if current is self.plot_tab:
            return self.plot_list
        if current is self.emu_tab:
            return self.emu_list
        if current is self.builder_tab:
            return self.builder_list
        if current is self.exp_tab:
            return self.exp_list
        return None

    def _ensure_selection(self, list_widget: QtWidgets.QListWidget | None) -> None:
        if list_widget is None:
            return
        if list_widget.count() and list_widget.currentRow() == -1:
            list_widget.setCurrentRow(0)

    def _focus_current_list(self, select_first: bool = False) -> None:
        list_widget = self._current_list_widget()
        if list_widget is None:
            return
        if select_first and list_widget.count() and list_widget.currentRow() == -1:
            list_widget.setCurrentRow(0)
        self._ensure_selection(list_widget)
        try:
            list_widget.setFocus(QtCore.Qt.FocusReason.TabFocusReason)
        except Exception:
            list_widget.setFocus()

    def _handle_tab_changed(self, _: int) -> None:
        list_widget = self._current_list_widget()
        self._ensure_selection(list_widget)
        focus_widget = QtWidgets.QApplication.focusWidget()
        if isinstance(focus_widget, QtWidgets.QTabBar):
            self._focus_current_list()

    def _sorted_names(self, category: str) -> list[str]:
        mapping = self._registry.get(category, {})
        names = list(mapping.keys())
        mode = self._sort_modes.get(category, "last_used")
        if mode == "name_asc":
            names.sort(key=str.casefold)
        elif mode == "name_desc":
            names.sort(key=str.casefold, reverse=True)
        else:
            names.sort(
                key=lambda name: (
                    -self._last_order(category, name),
                    -self._launcher_last_used(category, name),
                    name.casefold(),
                )
            )
        return names

    def _launcher_last_used(self, category: str, name: str) -> float:
        value = self._settings.value(f"launcher_last_used/{category}/{name}")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _load_last_order_counter(self) -> int:
        raw = self._settings.value("launcher_last_order/seq", 0)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    def _last_order(self, category: str, name: str) -> int:
        value = self._settings.value(f"launcher_last_order/{category}/{name}", 0)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _update_last_used(self, category: str, name: str) -> None:
        now = time.time()
        self._settings.setValue(f"launcher_last_used/{category}/{name}", now)
        # Keep legacy key in sync for backward compatibility with older builds.
        self._settings.setValue(f"last_used/{category}/{name}", now)
        self._last_order_counter = max(0, int(getattr(self, "_last_order_counter", 0))) + 1
        self._settings.setValue("launcher_last_order/seq", self._last_order_counter)
        self._settings.setValue(
            f"launcher_last_order/{category}/{name}",
            self._last_order_counter,
        )

    def _set_sort_mode(self, category: str, mode: str) -> None:
        if category not in self._list_widgets:
            return
        if mode not in {"last_used", "name_asc", "name_desc"}:
            return
        current_item = self._list_widgets[category].currentItem()
        selected = current_item.text() if current_item is not None else None
        self._sort_modes[category] = mode
        self._settings.setValue(f"sort/{category}", mode)
        self._refresh_list(category, select_name=selected)

    def _handle_sort_trigger(self, action: QtGui.QAction) -> None:
        data = action.data()
        if isinstance(data, tuple) and len(data) == 2:
            category, mode = data
            self._set_sort_mode(str(category), str(mode))

    def _advance_tab(self, offset: int) -> bool:
        count = self.tabs.count()
        if count <= 1:
            return False
        current_index = self.tabs.currentIndex()
        if current_index < 0:
            return False
        new_index = (current_index + offset) % count
        if new_index == current_index:
            return False
        self.tabs.setCurrentIndex(new_index)
        self._focus_current_list(select_first=True)
        return True

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        try:
            etype = event.type()
        except RecursionError:
            return False
        if etype == QtCore.QEvent.Type.KeyPress and isinstance(event, QtGui.QKeyEvent):
            key_event = cast(QtGui.QKeyEvent, event)
            focus_widget = QtWidgets.QApplication.focusWidget()
            if focus_widget is not None and not self.isAncestorOf(focus_widget):
                return super().eventFilter(obj, event)
            if not self.isActiveWindow():
                return super().eventFilter(obj, event)
            key = key_event.key()
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                list_widget = self._current_list_widget()
                self._ensure_selection(list_widget)
                if list_widget is not None and list_widget.count():
                    if list_widget.currentRow() == -1:
                        list_widget.setCurrentRow(0)
                self.run_selected()
                event.accept()
                return True
            if key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
                if isinstance(focus_widget, QtWidgets.QLineEdit):
                    return super().eventFilter(obj, event)
                direction = -1 if key == QtCore.Qt.Key.Key_Left else 1
                if self._advance_tab(direction):
                    event.accept()
                    return True
            if key in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down):
                list_widget = self._current_list_widget()
                if list_widget is None or list_widget.count() == 0:
                    return super().eventFilter(obj, event)
                if isinstance(focus_widget, QtWidgets.QLineEdit):
                    if key == QtCore.Qt.Key.Key_Down:
                        self._focus_current_list(select_first=True)
                        event.accept()
                        return True
                    return super().eventFilter(obj, event)
                if focus_widget is list_widget:
                    return super().eventFilter(obj, event)
                current_row = list_widget.currentRow()
                if current_row == -1:
                    new_row = 0 if key == QtCore.Qt.Key.Key_Down else list_widget.count() - 1
                elif key == QtCore.Qt.Key.Key_Down:
                    new_row = min(current_row + 1, list_widget.count() - 1)
                else:
                    new_row = max(current_row - 1, 0)
                list_widget.setCurrentRow(new_row)
                self._focus_current_list()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def run_selected(self) -> None:
        if not self._registry_loaded:
            return
        category: str | None = None
        item: QtWidgets.QListWidgetItem | None
        if self.tabs.currentWidget() is self.log_tab:
            category = "loggers"
            item = self.log_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a logger")
                return
        elif self.tabs.currentWidget() is self.plot_tab:
            category = "plotters"
            item = self.plot_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a plotting script")
                return
        elif self.tabs.currentWidget() is self.emu_tab:
            category = "emulators"
            item = self.emu_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select an emulator")
                return
        elif self.tabs.currentWidget() is self.builder_tab:
            category = "builders"
            item = self.builder_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(
                    self, "No selection", "Please select a builder tool"
                )
                return
        elif self.tabs.currentWidget() is self.exp_tab:
            category = "experiments"
            item = self.exp_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.information(
                    self, "No selection", "Enable and pick an experiment to launch"
                )
                return
        else:
            return

        assert item is not None
        assert category is not None
        item_text = item.text()
        registry = self._registry.get(category, {})
        func = registry.get(item_text)
        if func is None:
            QtWidgets.QMessageBox.critical(
                self,
                "Missing entry",
                f"No handler registered for {item_text}",
            )
            return
        _reset_outlier_flags()

        app_instance = QtWidgets.QApplication.instance()
        assert isinstance(app_instance, QtWidgets.QApplication)

        existing_windows = set(app_instance.topLevelWidgets())

        result: QtWidgets.QWidget | None = None

        try:
            result = func()
            if isinstance(result, QtWidgets.QWidget):
                self._register_window(result)
        except SystemExit as exc:
            code = exc.code
            if code not in (None, 0):
                QtWidgets.QMessageBox.critical(self, "Error", str(code))
        except Exception as exc:  # pragma: no cover - unexpected errors
            QtWidgets.QMessageBox.critical(
                self, "Error", f"{type(exc).__name__}: {exc}"
            )

        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

        new_windows = [
            w for w in app_instance.topLevelWidgets() if w not in existing_windows
        ]
        if isinstance(result, QtWidgets.QWidget) and result not in new_windows:
            new_windows.append(result)
        for w in new_windows:
            try:
                w.raise_()
                w.activateWindow()
            except RuntimeError:
                pass
            if isinstance(w, QtWidgets.QWidget):
                self._register_window(w)

        for w in app_instance.topLevelWidgets():
            if w is self:
                continue
            if isinstance(w, QtWidgets.QWidget):
                self._register_window(w)

        self._update_last_used(category, item_text)
        self._refresh_list(category, select_name=item_text)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        open_windows = [w for w in list(self._open_windows) if isinstance(w, QtWidgets.QWidget) and w.isVisible()]
        if open_windows:
            reply = QtWidgets.QMessageBox.question(
                self,
                "Close Launcher",
                f"Closing the launcher will also close {len(open_windows)} open window(s). Continue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                self._closing = False
                event.ignore()
                return
            for w in list(open_windows):
                try:
                    w.close()
                except Exception:
                    pass
        self._closing = True
        event.accept()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
            QtCore.QTimer.singleShot(0, app.quit)


def main(argv: list[str] | None = None) -> None:
    argv_list = list(sys.argv if argv is None else argv)
    args, qt_args = _parse_launcher_args(argv_list[1:])
    if args.visual_check:
        raise SystemExit(_run_visual_check(args))

    # Ensure a GUI platform plugin is used (not an offscreen one from tests)
    # Some test environments set QT_QPA_PLATFORM=offscreen. If that leaks into
    # an interactive run, Qt's style engine may try to paint using QPainter on
    # an invalid device, producing warnings like "QPainter::begin: Paint device
    # returned engine == 0". Clear it so the default (e.g. 'windows') is used.
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal", "headless"}:
        os.environ.pop("QT_QPA_PLATFORM", None)

    app = QtWidgets.QApplication([argv_list[0], *qt_args])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("PyPlot Launcher")
    _schedule_theme_application(app)
    icon = _create_launcher_icon()
    app.setWindowIcon(icon)
    placeholder = QtWidgets.QMainWindow()
    placeholder.setWindowIcon(icon)
    placeholder.setWindowTitle("PyPlot Launcher")
    placeholder.resize(420, 260)
    loading_label = QtWidgets.QLabel("Loading PyPlot Launcher...", placeholder)
    loading_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    loading_label.setStyleSheet("font-size: 16px; font-weight: 600;")
    placeholder.setCentralWidget(loading_label)
    placeholder.show()
    try:
        app.processEvents()
    except Exception:
        pass

    launcher_holder: dict[str, MasterLauncher] = {}

    def _create_launcher() -> None:
        window = MasterLauncher()
        launcher_holder["window"] = window

        def _show_when_ready() -> None:
            window.ready.disconnect(_show_when_ready)
            window.show()
            placeholder.close()

        window.ready.connect(_show_when_ready)

    def _fallback_show() -> None:
        window = launcher_holder.get("window")
        if isinstance(window, MasterLauncher) and not window.isVisible():
            window.show()
            placeholder.close()

    QtCore.QTimer.singleShot(0, _create_launcher)
    QtCore.QTimer.singleShot(5000, _fallback_show)
    app.exec()


if __name__ == "__main__":
    main()
