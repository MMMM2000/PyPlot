from __future__ import annotations

import sys
import os
import time
from importlib import import_module
from typing import Any, Callable, Dict, Tuple, cast

from PyQt6 import QtWidgets, QtGui, QtCore

from plotting import common
from plotting.shared.utils import install_standard_menu, developer_options
from plotting.shared.theme import ensure_app_theme
from plotting.pyplot.app import main as pyplot_main, PLUGIN_CLASS_REGISTRY
from experiments import EXPERIMENTS


LauncherFactory = Callable[..., QtWidgets.QWidget | None]


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


def launch_pyplot(initial: str | None = None) -> QtWidgets.QWidget | None:
    """Open the base plotter workbench, optionally selecting a script."""

    return pyplot_main(initial_plotter=initial)


PLUGIN_DISPLAY_NAMES = sorted(PLUGIN_CLASS_REGISTRY.keys())

PLOTTERS: Dict[str, LauncherFactory] = {"PyPlot": lambda: launch_pyplot()}
for _name in PLUGIN_DISPLAY_NAMES:
    PLOTTERS[_name] = (lambda n=_name: launch_pyplot(initial=n))

LOGGERS: Dict[str, LauncherFactory] = {
    "Serial Data Logger": _lazy("data_logging.data_logger", "main"),
    "Current Annealing Logger": _lazy(
        "data_logging.current_annealing_logger", "main"
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Master Launcher")
        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Ensure window bookkeeping exists even if later setup fails so the
        # destroyed callbacks can run safely.
        self._open_windows: list[QtWidgets.QWidget] = []

        self._settings = QtCore.QSettings("MicrowireData", "Launcher")
        self.dev_opts = developer_options()
        self._closing = False

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
        if BUILDERS:
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

        self._registry: dict[str, Dict[str, LauncherFactory]] = {
            "loggers": LOGGERS,
            "plotters": PLOTTERS,
            "emulators": EMULATORS,
        }
        if BUILDERS:
            self._registry["builders"] = BUILDERS
        if EXPERIMENTS:
            self._registry["experiments"] = EXPERIMENTS

        self._category_labels = {
            "loggers": "Loggers",
            "plotters": "Plotting",
            "emulators": "Emulators",
        }
        if BUILDERS:
            self._category_labels["builders"] = "Builders"
        if "experiments" in self._registry:
            self._category_labels["experiments"] = "Experiments"

        self._list_widgets = {
            "loggers": self.log_list,
            "plotters": self.plot_list,
            "emulators": self.emu_list,
        }
        if BUILDERS:
            self._list_widgets["builders"] = self.builder_list
        if "experiments" in self._registry:
            self._list_widgets["experiments"] = self.exp_list

        self._sort_modes: dict[str, str] = {}
        for category in self._list_widgets:
            stored = self._settings.value(f"sort/{category}", "last_used")
            if not isinstance(stored, str) or stored not in {"last_used", "name_asc", "name_desc"}:
                stored = "last_used"
            self._sort_modes[category] = stored
        self._sort_groups: dict[str, QtGui.QActionGroup] = {}

        self.main_layout.addWidget(self.search_bar)
        self.main_layout.addWidget(self.tabs)

        self._refresh_all_lists()
        if self.dev_opts.show_experiments() and self.exp_list.count():
            self._experiments_index = self.tabs.addTab(self.exp_tab, "Experiments")
        self.dev_opts.experiments_visibility_changed.connect(self._sync_experiments_tab)
        self.search_bar.textChanged.connect(self._apply_search_filter)
        self.tabs.currentChanged.connect(self._handle_tab_changed)

        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.run_button)

        self.main_layout.addLayout(button_row)

        menu_bar = install_standard_menu(
            self,
            help_topic="launcher",
            close_window=self._close_launcher,
        )
        sort_menu = menu_bar.addMenu("&Sort")
        if sort_menu is None:
            sort_menu = QtWidgets.QMenu("&Sort", self)
            menu_bar.addMenu(sort_menu)
        self._install_sort_menu(sort_menu)

    def _close_launcher(self) -> None:
        """Close hook that satisfies :func:`install_standard_menu`."""

        # ``QWidget.close`` returns ``bool`` and Pylance/Pyright expect the menu
        # callback to return ``None``.  We call the underlying method but
        # intentionally drop the return value to keep the type contract tidy.
        self.close()

    def _restore_launcher(self) -> None:
        if self._closing:
            return
        if not self.isVisible():
            self.show()
            try:
                self.raise_()
                self.activateWindow()
            except Exception:
                pass

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
            names.sort(key=lambda name: (-self._last_used(category, name), name.casefold()))
        return names

    def _last_used(self, category: str, name: str) -> float:
        value = self._settings.value(f"last_used/{category}/{name}")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _update_last_used(self, category: str, name: str) -> None:
        self._settings.setValue(f"last_used/{category}/{name}", time.time())

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
        if event.type() == QtCore.QEvent.Type.KeyPress:
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
        common.CHECK_OUTLIERS = False
        common.AUTO_REMOVE_OUTLIERS = False

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


def main() -> None:
    # Ensure a GUI platform plugin is used (not an offscreen one from tests)
    # Some test environments set QT_QPA_PLATFORM=offscreen. If that leaks into
    # an interactive run, Qt's style engine may try to paint using QPainter on
    # an invalid device, producing warnings like "QPainter::begin: Paint device
    # returned engine == 0". Clear it so the default (e.g. 'windows') is used.
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal", "headless"}:
        os.environ.pop("QT_QPA_PLATFORM", None)

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ensure_app_theme(app)
    placeholder = QtWidgets.QMainWindow()
    placeholder.setWindowTitle("PyPlot Launcher")
    placeholder.resize(420, 260)
    loading_label = QtWidgets.QLabel("Loading PyPlot Launcher…", placeholder)
    loading_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    loading_label.setStyleSheet("font-size: 16px; font-weight: 600;")
    placeholder.setCentralWidget(loading_label)
    placeholder.show()
    try:
        app.processEvents()
    except Exception:
        pass

    launcher_holder: dict[str, MasterLauncher] = {}

    def _launch() -> None:
        window = MasterLauncher()
        launcher_holder["window"] = window
        window.show()
        placeholder.close()

    QtCore.QTimer.singleShot(0, _launch)
    app.exec()


if __name__ == "__main__":
    main()
