from __future__ import annotations

import sys
import os
from typing import Callable, Dict

from PyQt6 import QtWidgets, QtGui

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


class MasterLauncher(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Master Launcher")
        self.main_layout = QtWidgets.QVBoxLayout(self)

        # Keep references to launched windows so they stay open when
        # the launcher calls their ``main`` functions.
        self._open_windows = []

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
        self.outlier_cb = QtWidgets.QCheckBox("Check for outliers")
        self.outlier_cb.setChecked(False)
        self.auto_outlier_cb = QtWidgets.QCheckBox("Automatically remove outliers")
        self.auto_outlier_cb.setChecked(False)
        self.auto_outlier_cb.setEnabled(False)
        self.outlier_cb.stateChanged.connect(
            lambda state: self.auto_outlier_cb.setEnabled(bool(state))
        )
        plot_layout.addWidget(self.outlier_cb)
        plot_layout.addWidget(self.auto_outlier_cb)

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

        self.main_layout.addWidget(self.tabs)
        self.main_layout.addLayout(theme_row)
        self.main_layout.addWidget(self.run_button)

    def on_theme_changed(self) -> None:
        app_instance = QtWidgets.QApplication.instance()
        assert isinstance(app_instance, QtWidgets.QApplication)
        idx = self.theme_combo.currentIndex()
        mode = ["system", "light", "dark"][idx]
        apply_theme(app_instance, mode)

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
            if item.text() in ("Hsw Distribution", "Hsw Load Compare"):
                common.CHECK_OUTLIERS = False
                common.AUTO_REMOVE_OUTLIERS = False
            else:
                common.CHECK_OUTLIERS = self.outlier_cb.isChecked()
                common.AUTO_REMOVE_OUTLIERS = self.auto_outlier_cb.isChecked()
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
        app_instance.setQuitOnLastWindowClosed(False)

        existing_windows = set(app_instance.topLevelWidgets())

        result: QtWidgets.QWidget | None = None

        try:
            result = func()
            if isinstance(result, QtWidgets.QWidget):
                self._open_windows.append(result)
                result.destroyed.connect(lambda: self._open_windows.remove(result))
        except SystemExit as exc:
            code = exc.code
            if code not in (None, 0):
                QtWidgets.QMessageBox.critical(self, "Error", str(code))
        except Exception as exc:  # pragma: no cover - unexpected errors
            QtWidgets.QMessageBox.critical(
                self, "Error", f"{type(exc).__name__}: {exc}"
            )
        finally:
            app_instance.setQuitOnLastWindowClosed(True)

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
                event.ignore()
                return
            for w in list(open_windows):
                try:
                    w.close()
                except Exception:
                    pass
        event.accept()


def main() -> None:
    # Ensure a GUI platform plugin is used (not an offscreen one from tests)
    # Some test environments set QT_QPA_PLATFORM=offscreen. If that leaks into
    # an interactive run, Qt's style engine may try to paint using QPainter on
    # an invalid device, producing warnings like "QPainter::begin: Paint device
    # returned engine == 0". Clear it so the default (e.g. 'windows') is used.
    if os.environ.get("QT_QPA_PLATFORM", "").lower() in {"offscreen", "minimal", "headless"}:
        os.environ.pop("QT_QPA_PLATFORM", None)

    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    dlg = MasterLauncher()
    dlg.show()
    app.exec()


if __name__ == "__main__":
    main()
