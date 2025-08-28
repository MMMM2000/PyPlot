from __future__ import annotations
import sys
from typing import List, Dict, Any

from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    # When executed directly, include the repository root in ``sys.path``
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.temperature_dependence import core as orig
    from plotting.utils import apply_system_theme, select_files_or_folder
else:
    from . import core as orig
    from ..utils import apply_system_theme, select_files_or_folder


def ask_user() -> tuple[List[str], Dict[str, Any]]:
    paths = select_files_or_folder()
    if not paths:
        sys.exit("No files selected.")

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Temperature Dependence Settings")
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
    backend_combo = QtWidgets.QComboBox(); backend_combo.addItems(["Matplotlib", "Origin", "Both"])
    backend_combo.setCurrentIndex(0)
    mode_combo = QtWidgets.QComboBox()
    mode_combo.addItems(["Raw", "Processed", "Both"])
    mode_map = {"raw": 0, "processed": 1, "both": 2}
    mode_combo.setCurrentIndex(mode_map.get(orig.PLOT_MODE, 0))
    out_dir_edit = QtWidgets.QLineEdit(orig.OUTPUT_DIR)
    browse_btn = QtWidgets.QPushButton("Browse")
    fmt_combo = QtWidgets.QComboBox(); fmt_combo.addItems(["png", "pdf", "svg"]); fmt_combo.setCurrentText(orig.SAVE_FORMAT)
    dpi_spin = QtWidgets.QSpinBox(); dpi_spin.setRange(72, 3000); dpi_spin.setValue(int(orig.PNG_DPI))

    def browse_out() -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Select output directory", out_dir_edit.text())
        if d:
            out_dir_edit.setText(d)

    browse_btn.clicked.connect(browse_out)

    out_group = QtWidgets.QGroupBox("Output")
    out_layout = QtWidgets.QGridLayout(out_group)
    out_layout.addWidget(show_cb, 0, 0)
    out_layout.addWidget(save_cb, 1, 0)
    out_layout.addWidget(QtWidgets.QLabel("Mode:"), 2, 0)
    out_layout.addWidget(mode_combo, 2, 1)
    out_layout.addWidget(QtWidgets.QLabel("Backend:"), 3, 0)
    out_layout.addWidget(backend_combo, 3, 1)
    out_layout.addWidget(QtWidgets.QLabel("Directory:"), 4, 0)
    out_layout.addWidget(out_dir_edit, 5, 0)
    out_layout.addWidget(browse_btn, 5, 1)
    out_layout.addWidget(QtWidgets.QLabel("Format:"), 6, 0)
    out_layout.addWidget(fmt_combo, 6, 1)
    out_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 7, 0)
    out_layout.addWidget(dpi_spin, 7, 1)

    proc_group = QtWidgets.QGroupBox("Processed curve")
    proc_layout = QtWidgets.QGridLayout(proc_group)
    med_spin = QtWidgets.QSpinBox(); med_spin.setRange(1, 9999); med_spin.setValue(int(orig.MED_WINDOW))
    ma_spin = QtWidgets.QSpinBox(); ma_spin.setRange(1, 9999); ma_spin.setValue(int(orig.MA_WINDOW))
    proc_layout.addWidget(QtWidgets.QLabel("Med window:"), 0, 0)
    proc_layout.addWidget(med_spin, 0, 1)
    proc_layout.addWidget(QtWidgets.QLabel("MA window:"), 0, 2)
    proc_layout.addWidget(ma_spin, 0, 3)

    run_btn = QtWidgets.QPushButton("Run")
    run_btn.clicked.connect(dialog.accept)

    layout.addWidget(var_group, 0, 0)
    layout.addWidget(out_group, 0, 1)
    layout.addWidget(proc_group, 1, 0, 1, 2)
    layout.addWidget(run_btn, 2, 0, 1, 2)
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
        "mode": {0: "raw", 1: "processed", 2: "both"}[mode_combo.currentIndex()],
        "out_dir": out_dir_edit.text(),
        "med_window": med_spin.value(),
        "ma_window": ma_spin.value(),
        "format": fmt_combo.currentText(),
        "dpi": dpi_spin.value(),
        "backend": ["matplotlib", "origin", "both"][backend_combo.currentIndex()],
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
    orig.PLOT_MODE = cfg["mode"]
    orig.OUTPUT_DIR = cfg["out_dir"]
    orig.MED_WINDOW = int(cfg["med_window"])
    orig.MA_WINDOW = int(cfg["ma_window"])
    orig.SAVE_FORMAT = cfg["format"]
    orig.PNG_DPI = int(cfg["dpi"])

    orig.main(paths, backend=cfg["backend"])


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_system_theme(app)
    main()
