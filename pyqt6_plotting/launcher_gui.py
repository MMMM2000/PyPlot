from __future__ import annotations
import sys
import pathlib
from typing import Callable, Dict

from PyQt6 import QtWidgets

# When executed directly, ensure the repository root is available for imports
if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
    from pyqt6_plotting.stress_dependence import stress_gui
    from pyqt6_plotting.hsw_load_compare import load_compare_gui
    from pyqt6_plotting.maxion_continuous import maxion_gui
    from pyqt6_plotting.hsw_distribution import distribution_gui
    from pyqt6_plotting.utils import apply_dark_theme
else:
    from .stress_dependence import stress_gui
    from .hsw_load_compare import load_compare_gui
    from .maxion_continuous import maxion_gui
    from .hsw_distribution import distribution_gui
    from .utils import apply_dark_theme


PLOTTERS: Dict[str, Callable[[], None]] = {
    "Stress Dependence": stress_gui.main,
    "Hsw Load Compare": load_compare_gui.main,
    "Maxion Continuous": maxion_gui.main,
    "Hsw Distribution": distribution_gui.main,
}


class Launcher(QtWidgets.QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Plotting Launcher")
        self.main_layout = QtWidgets.QVBoxLayout(self)

        self.list_widget = QtWidgets.QListWidget()
        for name in PLOTTERS:
            self.list_widget.addItem(name)
        self.list_widget.setCurrentRow(0)

        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self.run_selected)

        self.main_layout.addWidget(self.list_widget)
        self.main_layout.addWidget(self.run_button)

    def run_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            QtWidgets.QMessageBox.warning(self, "No selection", "Please select a plotting script")
            return
        name = item.text()
        func = PLOTTERS[name]
        app_instance = QtWidgets.QApplication.instance()
        assert isinstance(app_instance, QtWidgets.QApplication)
        app_instance.setQuitOnLastWindowClosed(False)
        self.close()
        try:
            func()
        except SystemExit as exc:
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
        except Exception as exc:  # pragma: no cover - unexpected errors
            QtWidgets.QMessageBox.critical(
                self, "Error", f"{type(exc).__name__}: {exc}"
            )


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    dlg = Launcher()
    dlg.show()
    app.exec()


if __name__ == "__main__":
    main()
