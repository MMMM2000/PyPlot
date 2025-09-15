from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.hysteresis_loops import core
    from plotting.utils import (
        apply_system_theme,
        create_file_widget,
        run_with_console,
        create_readability_group,
        sync_readability,
        arrange_top_layout,
    )
else:
    from . import core
    from ..utils import (
        apply_system_theme,
        create_file_widget,
        run_with_console,
        create_readability_group,
        sync_readability,
        arrange_top_layout,
    )


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hysteresis Loop Settings")

        self.files, file_widget = create_file_widget(self, ext=".dat", key="hysteresis_loops")
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)

        left = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(left)

        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Combined", "Separate", "Stacked"])
        self.backend_combo = QtWidgets.QComboBox(); self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])

        mode_group = QtWidgets.QGroupBox("Options")
        mode_layout = QtWidgets.QGridLayout(mode_group)
        mode_layout.addWidget(QtWidgets.QLabel("Plot mode:"), 0, 0)
        mode_layout.addWidget(self.mode_combo, 0, 1)
        mode_layout.addWidget(QtWidgets.QLabel("Backend:"), 1, 0)
        mode_layout.addWidget(self.backend_combo, 1, 1)

        self.read_ctrl, read_group = create_readability_group("hysteresis_loops", core)
        mode_layout.addWidget(read_group, 2, 0, 1, 2)

        self.run_btn = QtWidgets.QPushButton("Plot")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(mode_group, 0, 0, 1, 2)
        layout.addWidget(self.run_btn, 1, 0, 1, 2)

        arrange_top_layout(self, file_widget, left, self.console)

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        mode = self.mode_combo.currentText()
        backend = ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()]
        sync_readability("hysteresis_loops", self.read_ctrl, core)
        run_with_console(lambda: core.plot_loops(self.files, mode=mode, show=True, backend=backend), self.console)


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

