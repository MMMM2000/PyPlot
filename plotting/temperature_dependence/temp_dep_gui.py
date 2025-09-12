from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.temperature_dependence import core as orig
    from plotting.utils import apply_system_theme, create_file_widget
else:
    from . import core as orig
    from ..utils import apply_system_theme, create_file_widget


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Temperature Dependence Settings")
        layout = QtWidgets.QGridLayout(self)

        self.files, file_widget = create_file_widget(self)
        layout.addWidget(file_widget, 0, 0, 1, 2)

        self.sum_cb = QtWidgets.QCheckBox("T1+T2"); self.sum_cb.setChecked(orig.PLOT_SUM)
        self.dt_cb = QtWidgets.QCheckBox("T2–T1"); self.dt_cb.setChecked(orig.PLOT_DT)
        self.t1_cb = QtWidgets.QCheckBox("T1"); self.t1_cb.setChecked(orig.PLOT_T1)
        self.t2_cb = QtWidgets.QCheckBox("T2"); self.t2_cb.setChecked(orig.PLOT_T2)

        var_group = QtWidgets.QGroupBox("Variables to plot")
        var_layout = QtWidgets.QVBoxLayout(var_group)
        for w in (self.sum_cb, self.dt_cb, self.t1_cb, self.t2_cb):
            var_layout.addWidget(w)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.backend_combo = QtWidgets.QComboBox(); self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        self.backend_combo.setCurrentIndex(0)
        self.mode_combo = QtWidgets.QComboBox(); self.mode_combo.addItems(["Raw", "Processed", "Both"])
        mode_map = {"raw": 0, "processed": 1, "both": 2}
        self.mode_combo.setCurrentIndex(mode_map.get(orig.PLOT_MODE, 0))
        self.out_dir_edit = QtWidgets.QLineEdit(orig.OUTPUT_DIR)
        browse_btn = QtWidgets.QPushButton("Browse")
        self.fmt_combo = QtWidgets.QComboBox(); self.fmt_combo.addItems(["png", "pdf", "svg"]); self.fmt_combo.setCurrentText(orig.SAVE_FORMAT)
        self.dpi_spin = QtWidgets.QSpinBox(); self.dpi_spin.setRange(72, 3000); self.dpi_spin.setValue(int(orig.PNG_DPI))

        def browse_out() -> None:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir_edit.text())
            if d:
                self.out_dir_edit.setText(d)

        browse_btn.clicked.connect(browse_out)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        out_layout.addWidget(self.show_cb, 0, 0)
        out_layout.addWidget(self.save_cb, 1, 0)
        out_layout.addWidget(QtWidgets.QLabel("Mode:"), 2, 0)
        out_layout.addWidget(self.mode_combo, 2, 1)
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 3, 0)
        out_layout.addWidget(self.backend_combo, 3, 1)
        out_layout.addWidget(QtWidgets.QLabel("Directory:"), 4, 0)
        out_layout.addWidget(self.out_dir_edit, 5, 0)
        out_layout.addWidget(browse_btn, 5, 1)
        out_layout.addWidget(QtWidgets.QLabel("Format:"), 6, 0)
        out_layout.addWidget(self.fmt_combo, 6, 1)
        out_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 7, 0)
        out_layout.addWidget(self.dpi_spin, 7, 1)

        proc_group = QtWidgets.QGroupBox("Processed curve")
        proc_layout = QtWidgets.QGridLayout(proc_group)
        self.med_spin = QtWidgets.QSpinBox(); self.med_spin.setRange(1, 9999); self.med_spin.setValue(int(orig.MED_WINDOW))
        self.ma_spin = QtWidgets.QSpinBox(); self.ma_spin.setRange(1, 9999); self.ma_spin.setValue(int(orig.MA_WINDOW))
        proc_layout.addWidget(QtWidgets.QLabel("Med window:"), 0, 0)
        proc_layout.addWidget(self.med_spin, 0, 1)
        proc_layout.addWidget(QtWidgets.QLabel("MA window:"), 0, 2)
        proc_layout.addWidget(self.ma_spin, 0, 3)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(var_group, 1, 0)
        layout.addWidget(out_group, 1, 1)
        layout.addWidget(proc_group, 2, 0, 1, 2)
        layout.addWidget(self.run_btn, 3, 0, 1, 2)

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
        orig.SHOW_PLOTS = self.show_cb.isChecked()
        orig.SAVE_PLOTS = self.save_cb.isChecked()
        orig.PLOT_MODE = {0: "raw", 1: "processed", 2: "both"}[self.mode_combo.currentIndex()]
        orig.OUTPUT_DIR = self.out_dir_edit.text()
        orig.MED_WINDOW = int(self.med_spin.value())
        orig.MA_WINDOW = int(self.ma_spin.value())
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = int(self.dpi_spin.value())
        backend = ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()]
        orig.main(self.files, backend=backend)


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

