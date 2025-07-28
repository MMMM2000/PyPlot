from __future__ import annotations
import sys
import os
from typing import List, Dict

from PyQt6 import QtWidgets, QtCore

from data_plotting import stress_dependence_plot as orig
from .utils import apply_dark_theme


def ask_user() -> tuple[List[str], Dict[str, object]]:
    paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
        None,
        "Select measurement files",
        "",
        "Text files (*.txt);;All files (*)",
    )
    if not paths:
        sys.exit("No files selected.")

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Stress Dependence Settings")
    layout = QtWidgets.QGridLayout(dialog)

    sum_cb = QtWidgets.QCheckBox("T1+T2"); sum_cb.setChecked(orig.PLOT_SUM)
    dt_cb  = QtWidgets.QCheckBox("T2–T1"); dt_cb.setChecked(orig.PLOT_DT)
    t1_cb  = QtWidgets.QCheckBox("T1"); t1_cb.setChecked(orig.PLOT_T1)
    t2_cb  = QtWidgets.QCheckBox("T2"); t2_cb.setChecked(orig.PLOT_T2)

    var_group = QtWidgets.QGroupBox("Variables to plot")
    var_layout = QtWidgets.QVBoxLayout(var_group)
    for w in (sum_cb, dt_cb, t1_cb, t2_cb):
        var_layout.addWidget(w)

    baseline_group = QtWidgets.QGroupBox("Baseline")
    bl_layout = QtWidgets.QVBoxLayout(baseline_group)
    first_rb = QtWidgets.QRadioButton("First")
    min_rb   = QtWidgets.QRadioButton("Min")
    if orig.BASELINE_MODE == "first":
        first_rb.setChecked(True)
    else:
        min_rb.setChecked(True)
    bl_layout.addWidget(first_rb)
    bl_layout.addWidget(min_rb)

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

    proc_group = QtWidgets.QGroupBox("Processed curve")
    proc_layout = QtWidgets.QGridLayout(proc_group)
    proc_cb = QtWidgets.QCheckBox("Plot processed"); proc_cb.setChecked(orig.PLOT_PROCESSED)
    med_spin = QtWidgets.QSpinBox(); med_spin.setRange(1, 9999); med_spin.setValue(orig.MED_WINDOW)
    ma_spin = QtWidgets.QSpinBox(); ma_spin.setRange(1, 9999); ma_spin.setValue(orig.MA_WINDOW)
    proc_layout.addWidget(proc_cb, 0, 0, 1, 4)
    proc_layout.addWidget(QtWidgets.QLabel("Med window:"), 1, 0)
    proc_layout.addWidget(med_spin, 1, 1)
    proc_layout.addWidget(QtWidgets.QLabel("MA window:"), 1, 2)
    proc_layout.addWidget(ma_spin, 1, 3)

    run_btn = QtWidgets.QPushButton("Run")
    run_btn.clicked.connect(dialog.accept)

    layout.addWidget(var_group, 0, 0)
    layout.addWidget(baseline_group, 0, 1)
    layout.addWidget(out_group, 1, 1)
    layout.addWidget(proc_group, 1, 0)
    layout.addWidget(run_btn, 2, 0, 1, 2)
    dialog.setLayout(layout)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        sys.exit(0)

    cfg = {
        "sum": sum_cb.isChecked(),
        "dT": dt_cb.isChecked(),
        "T1": t1_cb.isChecked(),
        "T2": t2_cb.isChecked(),
        "baseline": "first" if first_rb.isChecked() else "min",
        "show": show_cb.isChecked(),
        "save": save_cb.isChecked(),
        "out_dir": out_dir_edit.text(),
        "processed": proc_cb.isChecked(),
        "med_window": med_spin.value(),
        "ma_window": ma_spin.value(),
    }
    return paths, cfg


class ProgressDialog:
    def __init__(self, total: int):
        self.dialog = QtWidgets.QProgressDialog("Saving plots...", "Cancel", 0, total)
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

    orig.BASELINE_MODE = cfg["baseline"]
    orig.SHOW_PLOTS = cfg["show"]
    orig.SAVE_PLOTS = cfg["save"]
    orig.OUTPUT_DIR = cfg["out_dir"]
    orig.PLOT_PROCESSED = cfg["processed"]
    orig.MED_WINDOW = cfg["med_window"]
    orig.MA_WINDOW = cfg["ma_window"]

    orig.main(paths)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    main()
    sys.exit()
