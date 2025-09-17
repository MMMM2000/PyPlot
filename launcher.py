from __future__ import annotations

import sys
import os
from typing import Callable, Dict

from PyQt6 import QtWidgets, QtGui, QtCore

from data_logging import data_logger
from data_logging.current_annealing_logger import current_annealing_logger
from data_logging import pyvisa_current_annealing_logger
from emulators import virtual_serial_emulator_gui
from plotting import common
from plotting.hsw_distribution import distribution_gui
from plotting.hsw_load_compare import load_compare_gui
from plotting.hysteresis_loops import loops_gui
from plotting.maxion_continuous import maxion_gui
from plotting.current_annealing import anneal_gui as current_anneal_gui
from plotting.pdf_plotter import pdf_gui
from plotting.stress_dependence import stress_gui
from plotting.stress_sensitivity import sens_gui
from plotting.temperature_dependence import temp_dep_gui
from plotting.temperature_sensitivity import temp_gui
from plotting.utils import apply_system_theme, apply_theme
from app_help import make_help_button


PLOTTERS: Dict[str, Callable[[], QtWidgets.QWidget | None]] = {
    "Stress Dependence": stress_gui.main,
    "Hsw Load Compare": load_compare_gui.main,
    "Maxion Continuous": maxion_gui.main,
    "Hsw Distribution": distribution_gui.main,
    "Temperature Sensitivity": temp_gui.main,
    "Temperature Dependence": temp_dep_gui.main,
    "Stress Sensitivity": sens_gui.main,
    "Current Annealing": current_anneal_gui.main,
    "PDF Plotter": pdf_gui.main,
    "Hysteresis Loops": loops_gui.main,
}

LOGGERS: Dict[str, Callable[..., QtWidgets.QWidget]] = {
    "Serial Data Logger": data_logger.main,
    "Current Annealing Logger": current_annealing_logger.main,
    "PyVISA Current Annealing Logger": pyvisa_current_annealing_logger.main,
}

EMULATORS: Dict[str, Callable[..., QtWidgets.QWidget | None]] = {
    "Universal Serial Emulator": virtual_serial_emulator_gui.main,
}


class MasterLauncher(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Master Launcher")
        self.main_layout = QtWidgets.QVBoxLayout(self)

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

        # Keep references to launched windows so they stay open when
        # the launcher calls their ``main`` functions.
        self._open_windows: list[QtWidgets.QWidget] = []

        self.tabs = QtWidgets.QTabWidget()
        self.log_tab = QtWidgets.QWidget()
        self.plot_tab = QtWidgets.QWidget()
        self.emu_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.log_tab, "Loggers")
        self.tabs.addTab(self.plot_tab, "Plotting")
        self.tabs.addTab(self.emu_tab, "Emulators")

        self.log_list = QtWidgets.QListWidget()
        for name in LOGGERS:
            self.log_list.addItem(name)
        self.log_list.setCurrentRow(0)
        log_layout = QtWidgets.QVBoxLayout(self.log_tab)
        log_layout.addWidget(self.log_list)

        self.plot_list = QtWidgets.QListWidget()
        for name in PLOTTERS:
            self.plot_list.addItem(name)
        self.plot_list.setCurrentRow(0)
        plot_layout = QtWidgets.QVBoxLayout(self.plot_tab)
        plot_layout.addWidget(self.plot_list)

        self.emu_list = QtWidgets.QListWidget()
        for name in EMULATORS:
            self.emu_list.addItem(name)
        self.emu_list.setCurrentRow(0)
        emu_layout = QtWidgets.QVBoxLayout(self.emu_tab)
        emu_layout.addWidget(self.emu_list)

        # Theme selector
        theme_row = QtWidgets.QHBoxLayout()
        theme_row.addStretch(1)
        theme_row.addWidget(QtWidgets.QLabel("Theme:"))
        self.theme_combo = QtWidgets.QComboBox(); self.theme_combo.addItems(["System", "Light", "Dark"])
        self.theme_combo.setCurrentIndex(0)
        self.theme_combo.currentIndexChanged.connect(self.on_theme_changed)
        theme_row.addWidget(self.theme_combo)

        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addWidget(make_help_button("launcher", self))
        button_row.addStretch(1)
        button_row.addWidget(self.run_button)

        self.main_layout.addWidget(self.tabs)
        self.main_layout.addLayout(theme_row)
        self.main_layout.addLayout(button_row)

    def on_theme_changed(self) -> None:
        app_instance = QtWidgets.QApplication.instance()
        assert isinstance(app_instance, QtWidgets.QApplication)
        idx = self.theme_combo.currentIndex()
        mode = ["system", "light", "dark"][idx]
        apply_theme(app_instance, mode)

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
            try:
                self._open_windows.remove(w)
            except ValueError:
                pass

        widget.destroyed.connect(_remove)

    def run_selected(self) -> None:
        if self.tabs.currentWidget() is self.log_tab:
            item = self.log_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a logger")
                return
            func = LOGGERS[item.text()]
            common.CHECK_OUTLIERS = False
            common.AUTO_REMOVE_OUTLIERS = False
        elif self.tabs.currentWidget() is self.plot_tab:
            item = self.plot_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a plotting script")
                return
            func = PLOTTERS[item.text()]
            common.CHECK_OUTLIERS = False
            common.AUTO_REMOVE_OUTLIERS = False
        else:
            item = self.emu_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select an emulator")
                return
            func = EMULATORS[item.text()]
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
    apply_system_theme(app)
    dlg = MasterLauncher()
    dlg.show()
    app.exec()


if __name__ == "__main__":
    main()

