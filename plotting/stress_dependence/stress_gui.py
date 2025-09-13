from __future__ import annotations

import sys
from typing import Any

from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.stress_dependence import core as orig
    from plotting.utils import (
        apply_system_theme,
        create_file_widget,
        prepare_output_dir,
        get_last_output_dir,
        set_last_output_dir,
        run_with_console,
        get_readability,
        set_readability,
        arrange_side_panel,
    )
else:
    from . import core as orig
    from ..utils import (
        apply_system_theme,
        create_file_widget,
        prepare_output_dir,
        get_last_output_dir,
        set_last_output_dir,
        run_with_console,
        get_readability,
        set_readability,
        arrange_side_panel,
    )


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stress Dependence Settings")

        self.files, file_widget = create_file_widget(self)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)

        left = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(left)

        self.sum_cb = QtWidgets.QCheckBox("T1+T2"); self.sum_cb.setChecked(orig.PLOT_SUM)
        self.dt_cb = QtWidgets.QCheckBox("T2–T1"); self.dt_cb.setChecked(orig.PLOT_DT)
        self.t1_cb = QtWidgets.QCheckBox("T1"); self.t1_cb.setChecked(orig.PLOT_T1)
        self.t2_cb = QtWidgets.QCheckBox("T2"); self.t2_cb.setChecked(orig.PLOT_T2)

        var_group = QtWidgets.QGroupBox("Variables to plot")
        var_layout = QtWidgets.QVBoxLayout(var_group)
        for w in (self.sum_cb, self.dt_cb, self.t1_cb, self.t2_cb):
            var_layout.addWidget(w)

        baseline_group = QtWidgets.QGroupBox("Baseline")
        bl_layout = QtWidgets.QVBoxLayout(baseline_group)
        self.first_rb = QtWidgets.QRadioButton("First")
        self.min_rb = QtWidgets.QRadioButton("Min")
        if orig.BASELINE_MODE == "first":
            self.first_rb.setChecked(True)
        else:
            self.min_rb.setChecked(True)
        bl_layout.addWidget(self.first_rb)
        bl_layout.addWidget(self.min_rb)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.out_dir_edit = QtWidgets.QLineEdit(get_last_output_dir())
        browse_btn = QtWidgets.QPushButton("Browse")

        def browse_out() -> None:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir_edit.text())
            if d:
                self.out_dir_edit.setText(d)

        browse_btn.clicked.connect(browse_out)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        self.backend_combo = QtWidgets.QComboBox(); self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        self.backend_combo.setCurrentIndex(0)
        out_layout.addWidget(self.show_cb, 0, 0)
        out_layout.addWidget(self.save_cb, 1, 0)
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 2, 0)
        out_layout.addWidget(self.backend_combo, 2, 1)
        self.fmt_combo = QtWidgets.QComboBox(); self.fmt_combo.addItems(["png", "pdf", "svg"]); self.fmt_combo.setCurrentText(orig.SAVE_FORMAT)
        self.dpi_spin = QtWidgets.QSpinBox(); self.dpi_spin.setRange(72, 3000); self.dpi_spin.setValue(int(orig.PNG_DPI))
        out_layout.addWidget(QtWidgets.QLabel("Format:"), 3, 0)
        out_layout.addWidget(self.fmt_combo, 3, 1)
        out_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 4, 0)
        out_layout.addWidget(self.dpi_spin, 4, 1)
        self.subdir_cb = QtWidgets.QCheckBox("Create subfolder")
        out_layout.addWidget(self.subdir_cb, 5, 0, 1, 2)
        out_layout.addWidget(QtWidgets.QLabel("Directory:"), 6, 0)
        out_layout.addWidget(self.out_dir_edit, 7, 0)
        out_layout.addWidget(browse_btn, 7, 1)

        proc_group = QtWidgets.QGroupBox("Processed curve")
        proc_layout = QtWidgets.QGridLayout(proc_group)
        self.proc_cb = QtWidgets.QCheckBox("Plot processed"); self.proc_cb.setChecked(orig.PLOT_PROCESSED)
        self.med_spin = QtWidgets.QSpinBox(); self.med_spin.setRange(1, 9999); self.med_spin.setValue(int(orig.MED_WINDOW))
        self.ma_spin = QtWidgets.QSpinBox(); self.ma_spin.setRange(1, 9999); self.ma_spin.setValue(int(orig.MA_WINDOW))
        proc_layout.addWidget(self.proc_cb, 0, 0, 1, 4)
        proc_layout.addWidget(QtWidgets.QLabel("Med window:"), 1, 0)
        proc_layout.addWidget(self.med_spin, 1, 1)
        proc_layout.addWidget(QtWidgets.QLabel("MA window:"), 1, 2)
        proc_layout.addWidget(self.ma_spin, 1, 3)

        self.read_cb = QtWidgets.QCheckBox("Improve readability")
        self.read_cb.setChecked(get_readability("stress_dependence"))
        read_group = QtWidgets.QGroupBox("Readability")
        read_layout = QtWidgets.QVBoxLayout(read_group)
        read_layout.addWidget(self.read_cb)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(var_group, 0, 0)
        layout.addWidget(baseline_group, 0, 1)
        layout.addWidget(proc_group, 1, 0)
        layout.addWidget(out_group, 1, 1)
        layout.addWidget(read_group, 2, 0, 1, 2)
        layout.addWidget(self.run_btn, 3, 0, 1, 2)

        arrange_side_panel(self, left, file_widget, self.console)

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        orig.PLOT_VARS.clear()
        if self.sum_cb.isChecked():
            orig.PLOT_VARS.append("sum")
        if self.dt_cb.isChecked():
            orig.PLOT_VARS.append("dT")
        if self.t1_cb.isChecked():
            orig.PLOT_VARS.append("T1")
        if self.t2_cb.isChecked():
            orig.PLOT_VARS.append("T2")
        orig.BASELINE_MODE = "first" if self.first_rb.isChecked() else "min"
        orig.SHOW_PLOTS = self.show_cb.isChecked()
        orig.SAVE_PLOTS = self.save_cb.isChecked()
        base = self.out_dir_edit.text()
        orig.OUTPUT_DIR = prepare_output_dir(base, "stress_dependence", self.subdir_cb.isChecked())
        set_last_output_dir(base)
        orig.IMPROVE_READABILITY = self.read_cb.isChecked()
        set_readability("stress_dependence", orig.IMPROVE_READABILITY)
        orig.PLOT_PROCESSED = self.proc_cb.isChecked()
        orig.MED_WINDOW = int(self.med_spin.value())
        orig.MA_WINDOW = int(self.ma_spin.value())
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = int(self.dpi_spin.value())
        backend = ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()]
        run_with_console(lambda: orig.main(self.files, backend=backend), self.console)


class ProgressDialog:
    def __init__(self, total: int):
        self.dialog = QtWidgets.QProgressDialog("Generating plots...", "Cancel", 0, total)
        self.dialog.setWindowTitle("Processing")
        self.dialog.canceled.connect(self.cancel)
        self.dialog.setAutoClose(False)
        self.dialog.setAutoReset(False)
        self.dialog.show()
        self.cancelled = False
        self.root = self

    def update(self) -> None:
        self.dialog.setValue(self.dialog.value() + 1)
        QtWidgets.QApplication.processEvents()

    def cancel(self) -> None:
        self.cancelled = True
        self.dialog.close()

    def destroy(self) -> None:
        self.dialog.close()


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        apply_system_theme(app)
        owns = True
    orig.ProgressDialog = ProgressDialog
    dlg = SettingsDialog()
    dlg.show()
    if owns:
        app.exec()


if __name__ == "__main__":
    main()

