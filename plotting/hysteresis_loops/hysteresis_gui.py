from __future__ import annotations
import os
import sys
import pathlib
from typing import List, Tuple

from PyQt6 import QtWidgets
import numpy as np
import matplotlib.pyplot as plt

# Support running both as a package module and as a standalone script
if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.utils import apply_system_theme
else:
    from ..utils import apply_system_theme


def _select_paths(parent: QtWidgets.QWidget | None = None) -> List[str]:
    """Let the user pick data files or a directory containing them."""
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
            "Select data files",
            "",
            "Data files (*.dat *.txt);;All files (*)",
        )
    elif clicked == folder_btn:
        directory = QtWidgets.QFileDialog.getExistingDirectory(parent, "Select folder")
        if directory:
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    if name.lower().endswith(('.dat', '.txt')):
                        paths.append(os.path.join(root, name))
            paths.sort()
    return paths


def _ask_mode(parent: QtWidgets.QWidget | None = None) -> str:
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle("Plot Mode")
    layout = QtWidgets.QVBoxLayout(dlg)
    combo = QtWidgets.QComboBox()
    combo.addItems(["Combined", "Separate", "Stacked"])
    layout.addWidget(combo)
    run_btn = QtWidgets.QPushButton("OK")
    run_btn.clicked.connect(dlg.accept)
    layout.addWidget(run_btn)
    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return ""
    return combo.currentText()


def _load_file(path: str) -> Tuple[np.ndarray, np.ndarray]:
    data = np.loadtxt(path, usecols=(0, 1))
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return data[:, 0], data[:, 1]


def main() -> QtWidgets.QWidget | None:
    paths = _select_paths()
    if not paths:
        sys.exit("No files selected.")
    mode = _ask_mode()
    if not mode:
        return None
    records: List[Tuple[str, np.ndarray, np.ndarray]] = []
    for p in paths:
        try:
            x, y = _load_file(p)
        except Exception:
            continue
        records.append((p, x, y))
    if not records:
        sys.exit("No valid data files.")
    if mode == "Combined":
        fig, ax = plt.subplots()
        for p, x, y in records:
            ax.plot(x, y, label=os.path.basename(p))
        ax.set_xlabel("H (A/m)")
        ax.set_ylabel("B (Wb)")
        ax.legend()
    elif mode == "Separate":
        for p, x, y in records:
            fig, ax = plt.subplots()
            ax.plot(x, y)
            ax.set_xlabel("H (A/m)")
            ax.set_ylabel("B (Wb)")
            ax.set_title(os.path.basename(p))
    else:  # Stacked
        n = len(records)
        fig, axes = plt.subplots(n, 1, sharex=True, figsize=(6, 2.5 * n))
        if n == 1:
            axes = [axes]
        for ax, (p, x, y) in zip(axes, records):
            ax.plot(x, y)
            ax.set_ylabel("B (Wb)")
            ax.set_title(os.path.basename(p))
            ax.grid(True, linestyle="--", alpha=0.3)
        axes[-1].set_xlabel("H (A/m)")
        fig.tight_layout()
    plt.show()
    return None


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    main()
    app.exec()
