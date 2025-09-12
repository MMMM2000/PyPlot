from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.current_annealing import core as orig
    from plotting.utils import apply_system_theme, create_file_widget
else:
    from . import core as orig
    from ..utils import apply_system_theme, create_file_widget


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Current Annealing Plot Settings")
        layout = QtWidgets.QGridLayout(self)

        self.files, file_widget = create_file_widget(self)
        layout.addWidget(file_widget, 0, 0, 1, 2)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.out_dir_edit = QtWidgets.QLineEdit(orig.OUTPUT_DIR)
        browse_btn = QtWidgets.QPushButton("Browse")
        self.backend_combo = QtWidgets.QComboBox(); self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])

        def browse() -> None:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir_edit.text())
            if d:
                self.out_dir_edit.setText(d)

        browse_btn.clicked.connect(browse)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        out_layout.addWidget(self.show_cb, 0, 0)
        out_layout.addWidget(self.save_cb, 1, 0)
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 2, 0)
        out_layout.addWidget(self.backend_combo, 2, 1)
        out_layout.addWidget(QtWidgets.QLabel("Directory:"), 3, 0)
        out_layout.addWidget(self.out_dir_edit, 4, 0)
        out_layout.addWidget(browse_btn, 4, 1)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(out_group, 1, 0, 1, 2)
        layout.addWidget(self.run_btn, 2, 0, 1, 2)

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        orig.SHOW_PLOTS = self.show_cb.isChecked()
        orig.SAVE_PLOTS = self.save_cb.isChecked()
        orig.OUTPUT_DIR = self.out_dir_edit.text()
        backend = ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()]
        orig.main(self.files, backend=backend)


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

