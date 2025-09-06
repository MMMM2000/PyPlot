from __future__ import annotations

import sys
import pathlib
from typing import List, Dict, Any

from PyQt6 import QtWidgets

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.current_annealing import core as orig
    from plotting.utils import apply_system_theme, select_files_or_folder
else:
    from . import core as orig
    from ..utils import apply_system_theme, select_files_or_folder


def ask_user() -> tuple[List[str], Dict[str, Any]]:
    paths = select_files_or_folder()
    if not paths:
        sys.exit("No files selected.")

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Current Annealing Plot Settings")
    layout = QtWidgets.QGridLayout(dialog)

    show_cb = QtWidgets.QCheckBox("Show plots"); show_cb.setChecked(orig.SHOW_PLOTS)
    save_cb = QtWidgets.QCheckBox("Save plots"); save_cb.setChecked(orig.SAVE_PLOTS)
    out_dir_edit = QtWidgets.QLineEdit(orig.OUTPUT_DIR)
    browse_btn = QtWidgets.QPushButton("Browse")
    backend_combo = QtWidgets.QComboBox(); backend_combo.addItems(["Matplotlib", "Origin", "Both"])  # output backend

    def browse() -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Select output directory", out_dir_edit.text())
        if d:
            out_dir_edit.setText(d)

    browse_btn.clicked.connect(browse)

    out_group = QtWidgets.QGroupBox("Output")
    out_layout = QtWidgets.QGridLayout(out_group)
    out_layout.addWidget(show_cb, 0, 0)
    out_layout.addWidget(save_cb, 1, 0)
    out_layout.addWidget(QtWidgets.QLabel("Backend:"), 2, 0)
    out_layout.addWidget(backend_combo, 2, 1)
    out_layout.addWidget(QtWidgets.QLabel("Directory:"), 3, 0)
    out_layout.addWidget(out_dir_edit, 4, 0)
    out_layout.addWidget(browse_btn, 4, 1)

    run_btn = QtWidgets.QPushButton("Run"); run_btn.clicked.connect(dialog.accept)
    layout.addWidget(out_group, 0, 0)
    layout.addWidget(run_btn, 1, 0)
    dialog.setLayout(layout)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        sys.exit(0)

    cfg = {
        "show": show_cb.isChecked(),
        "save": save_cb.isChecked(),
        "out_dir": out_dir_edit.text(),
        "backend": ["matplotlib", "origin", "both"][backend_combo.currentIndex()],
    }
    return paths, cfg


def main() -> None:
    paths, cfg = ask_user()
    orig.SHOW_PLOTS = cfg["show"]
    orig.SAVE_PLOTS = cfg["save"]
    orig.OUTPUT_DIR = cfg["out_dir"]
    orig.main(paths, backend=cfg["backend"])


if __name__ == "__main__":  # pragma: no cover
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    main()

