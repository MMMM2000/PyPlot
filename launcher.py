from __future__ import annotations
import sys
import pathlib
from typing import Callable, Dict

from PyQt6 import QtWidgets

# Make sure repository root is on sys.path when executed directly
if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parent))
    from pyqt6_plotting.stress_dependence import stress_gui
    from pyqt6_plotting.hsw_load_compare import load_compare_gui
    from pyqt6_plotting.maxion_continuous import maxion_gui
    from pyqt6_plotting.hsw_distribution import distribution_gui
    from pyqt6_plotting.utils import apply_dark_theme
    from pyqt6_logger import data_logger
else:
    from .pyqt6_plotting.stress_dependence import stress_gui
    from .pyqt6_plotting.hsw_load_compare import load_compare_gui
    from .pyqt6_plotting.maxion_continuous import maxion_gui
    from .pyqt6_plotting.hsw_distribution import distribution_gui
    from .pyqt6_plotting.utils import apply_dark_theme
    from .pyqt6_logger import data_logger


PLOTTERS: Dict[str, Callable[[], None]] = {
    "Stress Dependence": stress_gui.main,
    "Hsw Load Compare": load_compare_gui.main,
    "Maxion Continuous": maxion_gui.main,
    "Hsw Distribution": distribution_gui.main,
}

LOGGERS: Dict[str, Callable[..., QtWidgets.QWidget]] = {
    "Serial Data Logger": data_logger.main,
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
        self.tabs.addTab(self.log_tab, "Loggers")
        self.tabs.addTab(self.plot_tab, "Plotting")

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

        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)

        self.main_layout.addWidget(self.tabs)
        self.main_layout.addWidget(self.run_button)

    def run_selected(self) -> None:
        if self.tabs.currentWidget() is self.log_tab:
            item = self.log_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a logger")
                return
            func = LOGGERS[item.text()]
        else:
            item = self.plot_list.currentItem()
            if item is None:
                QtWidgets.QMessageBox.warning(self, "No selection", "Please select a plotting script")
                return
            func = PLOTTERS[item.text()]

        app_instance = QtWidgets.QApplication.instance()
        assert isinstance(app_instance, QtWidgets.QApplication)
        app_instance.setQuitOnLastWindowClosed(False)
        self.hide()
        try:
            result = func()
            if result is not None:
                self._open_windows.append(result)
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
            self.show()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    dlg = MasterLauncher()
    dlg.show()
    app.exec()


if __name__ == "__main__":
    main()
