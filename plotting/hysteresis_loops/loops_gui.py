from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.hysteresis_loops import core
    from plotting.utils import apply_system_theme, create_file_widget
else:
    from . import core
    from ..utils import apply_system_theme, create_file_widget


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hysteresis Loop Settings")
        layout = QtWidgets.QGridLayout(self)

        self.files, file_widget = create_file_widget(self, ext=".dat")
        layout.addWidget(file_widget, 0, 0, 1, 2)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Combined", "Separate", "Stacked"])
        self.backend_combo = QtWidgets.QComboBox(); self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])

        mode_group = QtWidgets.QGroupBox("Options")
        mode_layout = QtWidgets.QGridLayout(mode_group)
        mode_layout.addWidget(QtWidgets.QLabel("Plot mode:"), 0, 0)
        mode_layout.addWidget(self.mode_combo, 0, 1)
        mode_layout.addWidget(QtWidgets.QLabel("Backend:"), 1, 0)
        mode_layout.addWidget(self.backend_combo, 1, 1)

        self.run_btn = QtWidgets.QPushButton("Plot")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(mode_group, 1, 0, 1, 2)
        layout.addWidget(self.run_btn, 2, 0, 1, 2)

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        mode = self.mode_combo.currentText()
        backend = ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()]
        core.plot_loops(self.files, mode=mode, show=True, backend=backend)


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        apply_system_theme(app)
        owns = True
    dlg = SettingsDialog()
    dlg.show()
    if owns:
        app.exec()


if __name__ == "__main__":
    main()

