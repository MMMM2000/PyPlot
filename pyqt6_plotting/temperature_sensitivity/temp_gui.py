from __future__ import annotations
import sys
from typing import List, Dict, Any

from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    # When executed directly, include the repository root in ``sys.path``
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from pyqt6_plotting.temperature_sensitivity import core as orig
    from pyqt6_plotting.utils import apply_dark_theme
else:
    from . import core as orig
    from ..utils import apply_dark_theme


def ask_user() -> tuple[List[str], Dict[str, Any]]:
    paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
        None,
        "Select measurement files",
        "",
        "Text files (*.txt);;All files (*)",
    )
    if not paths:
        sys.exit("No files selected.")

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Temperature Sensitivity Settings")
    layout = QtWidgets.QGridLayout(dialog)

    sum_cb = QtWidgets.QCheckBox("T1+T2"); sum_cb.setChecked(orig.PLOT_SUM)
    dt_cb  = QtWidgets.QCheckBox("T2–T1"); dt_cb.setChecked(orig.PLOT_DT)
    t1_cb  = QtWidgets.QCheckBox("T1"); t1_cb.setChecked(orig.PLOT_T1)
    t2_cb  = QtWidgets.QCheckBox("T2"); t2_cb.setChecked(orig.PLOT_T2)

    var_group = QtWidgets.QGroupBox("Variables to plot")
    var_layout = QtWidgets.QVBoxLayout(var_group)
    for w in (sum_cb, dt_cb, t1_cb, t2_cb):
        var_layout.addWidget(w)

    show_cb = QtWidgets.QCheckBox("Show plots"); show_cb.setChecked(orig.SHOW_PLOTS)
    save_cb = QtWidgets.QCheckBox("Save plots"); save_cb.setChecked(orig.SAVE_PLOTS)
    baseline_combo = QtWidgets.QComboBox()
    baseline_combo.addItems(["None", "Zero 25\u00b0C", "Both"])
    baseline_map = {"none": 0, "zero_25": 1, "both": 2}
    baseline_combo.setCurrentIndex(baseline_map.get(orig.BASELINE_MODE, 0))
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
    out_layout.addWidget(QtWidgets.QLabel("Baseline:"), 2, 0)
    out_layout.addWidget(baseline_combo, 2, 1)
    out_layout.addWidget(QtWidgets.QLabel("Directory:"), 3, 0)
    out_layout.addWidget(out_dir_edit, 4, 0)
    out_layout.addWidget(browse_btn, 4, 1)

    run_btn = QtWidgets.QPushButton("Run")
    run_btn.clicked.connect(dialog.accept)

    layout.addWidget(var_group, 0, 0)
    layout.addWidget(out_group, 0, 1)
    layout.addWidget(run_btn, 1, 0, 1, 2)
    dialog.setLayout(layout)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        sys.exit(0)

    cfg = {
        "sum": sum_cb.isChecked(),
        "dT": dt_cb.isChecked(),
        "T1": t1_cb.isChecked(),
        "T2": t2_cb.isChecked(),
        "show": show_cb.isChecked(),
        "save": save_cb.isChecked(),
        "baseline": {0: "none", 1: "zero_25", 2: "both"}[baseline_combo.currentIndex()],
        "out_dir": out_dir_edit.text(),
    }
    return paths, cfg


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
    orig.PLOT_VARS.clear()
    if cfg["sum"]:
        orig.PLOT_VARS.append("sum")
    if cfg["dT"]:
        orig.PLOT_VARS.append("dT")
    if cfg["T1"]:
        orig.PLOT_VARS.append("T1")
    if cfg["T2"]:
        orig.PLOT_VARS.append("T2")

    orig.SHOW_PLOTS = cfg["show"]
    orig.SAVE_PLOTS = cfg["save"]
    orig.BASELINE_MODE = cfg["baseline"]
    orig.OUTPUT_DIR = cfg["out_dir"]

    orig.main(paths)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    main()
