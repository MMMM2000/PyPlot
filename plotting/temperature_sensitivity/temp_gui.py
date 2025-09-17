from __future__ import annotations

import sys
from PyQt6 import QtWidgets, QtGui, QtCore

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.temperature_sensitivity import core as orig
    from plotting.utils import (
        ensure_app_theme,
        create_file_widget,
        prepare_output_dir,
        get_last_output_dir,
        set_last_output_dir,
        run_with_console,
        create_readability_group,
        sync_readability,
        arrange_top_layout,
        restore_backend_choice,
        store_backend_choice,
        selected_backend,
        restore_png_dpi,
        store_png_dpi,
    )
    from plotting.utils import release_origin
else:
    from . import core as orig
    from ..utils import (
        ensure_app_theme,
        create_file_widget,
        prepare_output_dir,
        get_last_output_dir,
        set_last_output_dir,
        run_with_console,
        create_readability_group,
        sync_readability,
        arrange_top_layout,
        restore_backend_choice,
        store_backend_choice,
        selected_backend,
        restore_png_dpi,
        store_png_dpi,
    )
    from ..utils import release_origin


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Temperature Sensitivity Settings")
        self._owns_app = False
        try:
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_QuitOnClose, False)
        except Exception:
            pass

        self.files, file_widget = create_file_widget(
            self, key="temperature_sensitivity", on_outlier_toggle=self._handle_outlier_toggle
        )
        self._preprocessed_data = None
        self._preprocessed_snapshot: tuple[str, ...] | None = None
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)

        left = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(left)

        self.sum_cb = QtWidgets.QCheckBox("T1+T2"); self.sum_cb.setChecked(orig.PLOT_SUM)
        # Use ASCII hyphen to avoid encoding issues on some systems
        self.dt_cb = QtWidgets.QCheckBox("T2-T1"); self.dt_cb.setChecked(orig.PLOT_DT)
        self.t1_cb = QtWidgets.QCheckBox("T1"); self.t1_cb.setChecked(orig.PLOT_T1)
        self.t2_cb = QtWidgets.QCheckBox("T2"); self.t2_cb.setChecked(orig.PLOT_T2)

        var_group = QtWidgets.QGroupBox("Variables to plot")
        var_layout = QtWidgets.QVBoxLayout(var_group)
        for w in (self.sum_cb, self.dt_cb, self.t1_cb, self.t2_cb):
            var_layout.addWidget(w)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        orig.BACKEND = restore_backend_choice(
            "temperature_sensitivity", self.backend_combo, getattr(orig, "BACKEND", "matplotlib")
        )
        # Correct capitalization and degree symbol
        self.baseline_combo = QtWidgets.QComboBox(); self.baseline_combo.addItems(["None", "Zero 25°C", "Both"])
        baseline_map = {"none": 0, "zero_25": 1, "both": 2}
        self.baseline_combo.setCurrentIndex(baseline_map.get(orig.BASELINE_MODE, 0))
        self.out_dir_edit = QtWidgets.QLineEdit(get_last_output_dir(key="temperature_sensitivity"))
        browse_btn = QtWidgets.QPushButton("Browse")

        def browse_out() -> None:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir_edit.text())
            if d:
                self.out_dir_edit.setText(d)

        browse_btn.clicked.connect(browse_out)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        out_layout.addWidget(self.show_cb, 0, 0)
        out_layout.addWidget(self.save_cb, 1, 0)
        out_layout.addWidget(QtWidgets.QLabel("Baseline:"), 2, 0)
        out_layout.addWidget(self.baseline_combo, 2, 1)
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 3, 0)
        out_layout.addWidget(self.backend_combo, 3, 1)
        self.fmt_combo = QtWidgets.QComboBox()
        self.fmt_combo.addItems(["png", "pdf", "svg"])
        self.fmt_combo.setCurrentText(orig.SAVE_FORMAT)
        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 3000)
        orig.PNG_DPI = restore_png_dpi(
            "temperature_sensitivity", self.dpi_spin, getattr(orig, "PNG_DPI", 1200)
        )
        out_layout.addWidget(QtWidgets.QLabel("Format:"), 4, 0)
        out_layout.addWidget(self.fmt_combo, 4, 1)
        out_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 5, 0)
        out_layout.addWidget(self.dpi_spin, 5, 1)
        self.subdir_cb = QtWidgets.QCheckBox("Create subfolder")
        out_layout.addWidget(self.subdir_cb, 6, 0, 1, 2)
        out_layout.addWidget(QtWidgets.QLabel("Directory:"), 7, 0)
        out_layout.addWidget(self.out_dir_edit, 8, 0)
        out_layout.addWidget(browse_btn, 8, 1)

        cont_group = QtWidgets.QGroupBox("Continuous data")
        cont_layout = QtWidgets.QGridLayout(cont_group)
        self.cont_cb = QtWidgets.QCheckBox("Include continuous data"); self.cont_cb.setChecked(orig.INCLUDE_CONTINUOUS)
        self.med_spin = QtWidgets.QSpinBox(); self.med_spin.setRange(1, 9999); self.med_spin.setValue(int(orig.MED_WINDOW))
        self.ma_spin = QtWidgets.QSpinBox(); self.ma_spin.setRange(1, 9999); self.ma_spin.setValue(int(orig.MA_WINDOW))
        cont_layout.addWidget(self.cont_cb, 0, 0, 1, 4)
        cont_layout.addWidget(QtWidgets.QLabel("Med window:"), 1, 0)
        cont_layout.addWidget(self.med_spin, 1, 1)
        cont_layout.addWidget(QtWidgets.QLabel("MA window:"), 1, 2)
        cont_layout.addWidget(self.ma_spin, 1, 3)

        self.read_ctrl, read_group = create_readability_group("temperature_sensitivity", orig)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(var_group, 0, 0)
        layout.addWidget(out_group, 0, 1)
        layout.addWidget(cont_group, 1, 0, 1, 2)
        layout.addWidget(read_group, 2, 0, 1, 2)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 12, 0, 0)
        btn_row.addStretch(1)
        btn_row.addWidget(self.run_btn)

        arrange_top_layout(
            self,
            file_widget,
            left,
            self.console,
            footer=btn_row,
            help_topic="plot_temperature_sensitivity",
        )

    def _handle_outlier_toggle(self, enabled: bool, files: list[str]) -> bool:
        if not enabled:
            self._preprocessed_data = None
            self._preprocessed_snapshot = None
            return True
        if not files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return False
        file_snapshot = tuple(sorted(files))
        load_list = list(file_snapshot)

        def _task() -> None:
            data = orig.load_data(load_list)
            cleaned = orig.handle_outliers(data)
            self._preprocessed_data = cleaned.copy(deep=True)
            self._preprocessed_snapshot = file_snapshot
            print("Outlier check complete.")

        try:
            run_with_console(_task, self.console)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Outlier Check Failed", str(exc))
            return False
        return True

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
        orig.BASELINE_MODE = {0: "none", 1: "zero_25", 2: "both"}[self.baseline_combo.currentIndex()]
        base = self.out_dir_edit.text()
        orig.OUTPUT_DIR = prepare_output_dir(base, "temperature_sensitivity", self.subdir_cb.isChecked())
        set_last_output_dir(base, key="temperature_sensitivity")
        sync_readability("temperature_sensitivity", self.read_ctrl, orig)
        orig.INCLUDE_CONTINUOUS = self.cont_cb.isChecked()
        orig.MED_WINDOW = int(self.med_spin.value())
        orig.MA_WINDOW = int(self.ma_spin.value())
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = store_png_dpi("temperature_sensitivity", int(self.dpi_spin.value()))
        backend = store_backend_choice(
            "temperature_sensitivity", selected_backend(self.backend_combo)
        )
        orig.BACKEND = backend
        snapshot = tuple(sorted(self.files))
        preloaded = None
        if self._preprocessed_data is not None and self._preprocessed_snapshot == snapshot:
            preloaded = self._preprocessed_data.copy(deep=True)
        run_with_console(
            lambda: orig.main(self.files, backend=backend, preprocessed_data=preloaded),
            self.console,
        )


    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        orig.common.CHECK_OUTLIERS = False
        orig.common.AUTO_REMOVE_OUTLIERS = False
        self._preprocessed_data = None
        self._preprocessed_snapshot = None
        release_origin()
        super().closeEvent(event)

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
        ensure_app_theme(app)
        owns = True
    orig.ProgressDialog = ProgressDialog
    dlg = SettingsDialog()
    dlg._owns_app = owns
    dlg.show()
    if owns:
        app.exec()


if __name__ == "__main__":
    main()
