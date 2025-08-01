from __future__ import annotations
import sys
from typing import List, Dict, Any

from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    # When executed directly, prepend the repository root to ``sys.path`` so
    # that absolute imports of the ``pyqt6_plotting`` package work correctly.
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from pyqt6_plotting.maxion_continuous import core as orig
    from pyqt6_plotting.utils import apply_system_theme, select_files_or_folder
else:
    from . import core as orig
    from ..utils import apply_system_theme, select_files_or_folder


def ask_user() -> tuple[List[str], Dict[str, Any]]:
    paths = select_files_or_folder()
    if not paths:
        sys.exit("No files selected.")

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Maxion Continuous Settings")
    layout = QtWidgets.QGridLayout(dialog)

    show_cb = QtWidgets.QCheckBox("Show plots"); show_cb.setChecked(orig.SHOW_PLOTS)
    save_cb = QtWidgets.QCheckBox("Save plots"); save_cb.setChecked(orig.SAVE_PLOTS)
    out_dir_edit = QtWidgets.QLineEdit(orig.OUTPUT_DIR)
    browse_btn = QtWidgets.QPushButton("Browse")

    def browse_out() -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Select output directory", out_dir_edit.text())
        if d:
            out_dir_edit.setText(d)

    browse_btn.clicked.connect(browse_out)

    out_group = QtWidgets.QGroupBox("Output")
    out_layout = QtWidgets.QGridLayout(out_group)
    out_layout.addWidget(show_cb, 0, 0)
    out_layout.addWidget(save_cb, 1, 0)
    out_layout.addWidget(QtWidgets.QLabel("Directory:"), 2, 0)
    out_layout.addWidget(out_dir_edit, 3, 0)
    out_layout.addWidget(browse_btn, 3, 1)

    mode_group = QtWidgets.QGroupBox("Data to plot")
    mode_layout = QtWidgets.QVBoxLayout(mode_group)
    raw_rb = QtWidgets.QRadioButton("Raw"); raw_rb.setChecked(orig.PLOT_MODE == "raw")
    proc_rb = QtWidgets.QRadioButton("Processed"); proc_rb.setChecked(orig.PLOT_MODE == "processed")
    both_rb = QtWidgets.QRadioButton("Both"); both_rb.setChecked(orig.PLOT_MODE == "both")
    mode_layout.addWidget(raw_rb)
    mode_layout.addWidget(proc_rb)
    mode_layout.addWidget(both_rb)

    proc_group = QtWidgets.QGroupBox("Processed curve")
    proc_layout = QtWidgets.QGridLayout(proc_group)
    med_spin = QtWidgets.QSpinBox(); med_spin.setRange(1, 9999); med_spin.setValue(int(orig.MED_WINDOW))
    ma_spin = QtWidgets.QSpinBox(); ma_spin.setRange(1, 9999); ma_spin.setValue(int(orig.MA_WINDOW))
    proc_layout.addWidget(QtWidgets.QLabel("Med window:"), 0, 0)
    proc_layout.addWidget(med_spin, 0, 1)
    proc_layout.addWidget(QtWidgets.QLabel("MA window:"), 0, 2)
    proc_layout.addWidget(ma_spin, 0, 3)

    style_group = QtWidgets.QGroupBox("Scatter")
    style_layout = QtWidgets.QGridLayout(style_group)
    marker_spin = QtWidgets.QDoubleSpinBox(); marker_spin.setRange(0.1, 99.9); marker_spin.setSingleStep(0.1); marker_spin.setValue(float(orig.MARKER_SIZE))
    style_layout.addWidget(QtWidgets.QLabel("Marker size:"), 0, 0)
    style_layout.addWidget(marker_spin, 0, 1)

    run_btn = QtWidgets.QPushButton("Run")
    run_btn.clicked.connect(dialog.accept)

    layout.addWidget(out_group, 0, 0)
    layout.addWidget(mode_group, 0, 1)
    layout.addWidget(proc_group, 1, 0)
    layout.addWidget(style_group, 1, 1)
    layout.addWidget(run_btn, 2, 0, 1, 2)
    dialog.setLayout(layout)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        sys.exit(0)

    cfg = {
        "show": show_cb.isChecked(),
        "save": save_cb.isChecked(),
        "out_dir": out_dir_edit.text(),
        "mode": "raw" if raw_rb.isChecked() else "processed" if proc_rb.isChecked() else "both",
        "marker": marker_spin.value(),
        "med_window": med_spin.value(),
        "ma_window": ma_spin.value(),
    }
    return list(paths), cfg


class ProgressDialog:
    def __init__(self, total: int):
        self.dialog = QtWidgets.QProgressDialog("Processing...", "Cancel", 0, total)
        self.dialog.setWindowTitle("Processing")
        self.dialog.canceled.connect(self.cancel)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.show()
        self.cancelled = False
        self.root = self

    def update(self):
        self.dialog.setValue(self.dialog.value() + 1)
        QtWidgets.QApplication.processEvents()

    def cancel(self):
        self.cancelled = True
        self.dialog.close()

    def destroy(self):
        self.dialog.close()


def main() -> None:
    paths, cfg = ask_user()
    orig.ProgressDialog = ProgressDialog

    orig.SHOW_PLOTS = cfg["show"]
    orig.SAVE_PLOTS = cfg["save"]
    orig.OUTPUT_DIR = cfg["out_dir"]
    orig.PLOT_MODE = cfg["mode"]
    orig.MARKER_SIZE = cfg["marker"]
    orig.MED_WINDOW = int(cfg["med_window"])
    orig.MA_WINDOW = int(cfg["ma_window"])

    orig.main(paths)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    main()
