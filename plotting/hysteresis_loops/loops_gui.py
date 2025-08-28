from __future__ import annotations

import os
import sys
import pathlib
from typing import List

from PyQt6 import QtWidgets

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.utils import apply_system_theme
    from plotting.hysteresis_loops import core
else:
    from ..utils import apply_system_theme
    from . import core


def select_dat_files(parent: QtWidgets.QWidget | None = None) -> List[str]:
    """Return a list of ``.dat`` files chosen by the user."""
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle("Select Input")
    box.setText("Choose input files or a folder with data")
    files_btn = box.addButton("Files", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    folder_btn = box.addButton("Folder", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
    box.exec()

    clicked = box.clickedButton()
    paths: List[str] = []
    if clicked == files_btn:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            parent,
            "Select measurement files",
            "",
            "Data files (*.dat);;All files (*)",
        )
    elif clicked == folder_btn:
        directory = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select folder")
        if directory:
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith(".dat"):
                        paths.append(os.path.join(root, name))
            paths.sort()
    return list(paths)


def ask_files() -> List[str]:
    paths = select_dat_files()
    if not paths:
        sys.exit("No files selected.")
    return paths


def ask_mode() -> tuple[str, str]:
    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Hysteresis Loop Settings")
    layout = QtWidgets.QVBoxLayout(dialog)
    combo = QtWidgets.QComboBox()
    combo.addItems(["Combined", "Separate", "Stacked"])
    backend_combo = QtWidgets.QComboBox(); backend_combo.addItems(["Matplotlib", "Origin", "Both"])
    layout.addWidget(QtWidgets.QLabel("Plot mode:"))
    layout.addWidget(combo)
    layout.addWidget(QtWidgets.QLabel("Backend:"))
    layout.addWidget(backend_combo)
    run_btn = QtWidgets.QPushButton("Plot")
    run_btn.clicked.connect(dialog.accept)
    layout.addWidget(run_btn)
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        sys.exit(0)
    return combo.currentText(), ["matplotlib", "origin", "both"][backend_combo.currentIndex()]


def main() -> None:
    files = ask_files()
    mode, backend = ask_mode()
    core.plot_loops(files, mode=mode, show=True, backend=backend)


if __name__ == "__main__":  # pragma: no cover
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    main()
