from __future__ import annotations

import sys
from PyQt6 import QtWidgets, QtCore

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.maxion_continuous import core as orig
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


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Maxion Continuous Settings")

        self.settings = QtCore.QSettings("microwire", "maxion_continuous")

        self.files, file_widget = create_file_widget(self, key="maxion_continuous")
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)

        left = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(left)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.out_dir_edit = QtWidgets.QLineEdit(get_last_output_dir(key="maxion_continuous"))
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
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        orig.BACKEND = restore_backend_choice(
            "maxion_continuous", self.backend_combo, getattr(orig, "BACKEND", "matplotlib")
        )
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 2, 0)
        out_layout.addWidget(self.backend_combo, 2, 1)
        self.fmt_combo = QtWidgets.QComboBox()
        self.fmt_combo.addItems(["png", "pdf", "svg"])
        self.fmt_combo.setCurrentText(orig.SAVE_FORMAT)
        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 3000)
        orig.PNG_DPI = restore_png_dpi(
            "maxion_continuous", self.dpi_spin, getattr(orig, "PNG_DPI", 1200)
        )
        out_layout.addWidget(QtWidgets.QLabel("Format:"), 3, 0)
        out_layout.addWidget(self.fmt_combo, 3, 1)
        out_layout.addWidget(QtWidgets.QLabel("PNG dpi:"), 4, 0)
        out_layout.addWidget(self.dpi_spin, 4, 1)
        self.subdir_cb = QtWidgets.QCheckBox("Create subfolder")
        out_layout.addWidget(self.subdir_cb, 5, 0, 1, 2)
        out_layout.addWidget(QtWidgets.QLabel("Directory:"), 6, 0)
        out_layout.addWidget(self.out_dir_edit, 7, 0)
        out_layout.addWidget(browse_btn, 7, 1)

        mode_group = QtWidgets.QGroupBox("Data to plot")
        mode_layout = QtWidgets.QVBoxLayout(mode_group)
        self.raw_rb = QtWidgets.QRadioButton("Raw"); self.raw_rb.setChecked(orig.PLOT_MODE == "raw")
        self.proc_rb = QtWidgets.QRadioButton("Processed"); self.proc_rb.setChecked(orig.PLOT_MODE == "processed")
        self.both_rb = QtWidgets.QRadioButton("Both"); self.both_rb.setChecked(orig.PLOT_MODE == "both")
        mode_layout.addWidget(self.raw_rb)
        mode_layout.addWidget(self.proc_rb)
        mode_layout.addWidget(self.both_rb)

        proc_group = QtWidgets.QGroupBox("Processed curve")
        proc_layout = QtWidgets.QGridLayout(proc_group)
        self.med_spin = QtWidgets.QSpinBox(); self.med_spin.setRange(1, 9999); self.med_spin.setValue(int(orig.MED_WINDOW))
        self.ma_spin = QtWidgets.QSpinBox(); self.ma_spin.setRange(1, 9999); self.ma_spin.setValue(int(orig.MA_WINDOW))
        proc_layout.addWidget(QtWidgets.QLabel("Med window:"), 0, 0)
        proc_layout.addWidget(self.med_spin, 0, 1)
        proc_layout.addWidget(QtWidgets.QLabel("MA window:"), 0, 2)
        proc_layout.addWidget(self.ma_spin, 0, 3)
        self.center_cb = QtWidgets.QCheckBox("Median at 0")
        self.center_cb.setChecked(bool(self.settings.value("center_median_y", orig.CENTER_MEDIAN_Y, type=bool)))
        proc_layout.addWidget(self.center_cb, 1, 0, 1, 4)
        self.center_source_combo = QtWidgets.QComboBox()
        self.center_source_combo.addItems(["Raw", "Processed"])
        self.center_source_combo.setCurrentText(
            self.settings.value("center_source", orig.CENTER_MEDIAN_SOURCE, type=str).capitalize()
        )
        self.center_source_combo.setEnabled(self.center_cb.isChecked())
        self.center_cb.toggled.connect(self.center_source_combo.setEnabled)
        proc_layout.addWidget(QtWidgets.QLabel("Use median from:"), 2, 0)
        proc_layout.addWidget(self.center_source_combo, 2, 1, 1, 3)

        style_group = QtWidgets.QGroupBox("Scatter")
        style_layout = QtWidgets.QGridLayout(style_group)
        self.marker_spin = QtWidgets.QDoubleSpinBox()
        self.marker_spin.setRange(0.1, 99.9)
        self.marker_spin.setSingleStep(0.1)
        self.marker_spin.setValue(float(orig.MARKER_SIZE))
        style_layout.addWidget(QtWidgets.QLabel("Marker size:"), 0, 0)
        style_layout.addWidget(self.marker_spin, 0, 1)

        self.read_ctrl, read_group = create_readability_group("maxion_continuous", orig)
        self.scale_x_cb = QtWidgets.QCheckBox("×10⁴")
        self.scale_x_cb.setChecked(
            bool(self.settings.value("scale_x", orig.SCALE_X_1E4, type=bool))
        )
        self.scale_y_cb = QtWidgets.QCheckBox("×10³")
        self.scale_y_cb.setChecked(
            bool(self.settings.value("scale_y", orig.SCALE_Y_1E3, type=bool))
        )
        lay = read_group.layout()
        row = lay.rowCount()
        lay.addWidget(QtWidgets.QLabel("Scale X:"), row, 0)
        lay.addWidget(self.scale_x_cb, row, 1)
        lay.addWidget(QtWidgets.QLabel("Scale Y:"), row + 1, 0)
        lay.addWidget(self.scale_y_cb, row + 1, 1)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(out_group)
        layout.addWidget(mode_group)
        layout.addWidget(proc_group)
        layout.addWidget(style_group)
        layout.addWidget(read_group)
        layout.addStretch()
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
            help_topic="plot_maxion",
        )

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        orig.SHOW_PLOTS = self.show_cb.isChecked()
        orig.SAVE_PLOTS = self.save_cb.isChecked()
        base = self.out_dir_edit.text()
        orig.OUTPUT_DIR = prepare_output_dir(base, "maxion_continuous", self.subdir_cb.isChecked())
        set_last_output_dir(base, key="maxion_continuous")
        orig.PLOT_MODE = "raw" if self.raw_rb.isChecked() else "processed" if self.proc_rb.isChecked() else "both"
        orig.MARKER_SIZE = self.marker_spin.value()
        orig.MED_WINDOW = int(self.med_spin.value())
        orig.MA_WINDOW = int(self.ma_spin.value())
        orig.CENTER_MEDIAN_Y = self.center_cb.isChecked()
        orig.CENTER_MEDIAN_SOURCE = self.center_source_combo.currentText().lower()
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = store_png_dpi("maxion_continuous", int(self.dpi_spin.value()))
        sync_readability("maxion_continuous", self.read_ctrl, orig)
        orig.SCALE_X_1E4 = self.scale_x_cb.isChecked()
        orig.SCALE_Y_1E3 = self.scale_y_cb.isChecked()
        self.settings.setValue("scale_x", orig.SCALE_X_1E4)
        self.settings.setValue("scale_y", orig.SCALE_Y_1E3)
        self.settings.setValue("center_median_y", orig.CENTER_MEDIAN_Y)
        self.settings.setValue("center_source", orig.CENTER_MEDIAN_SOURCE)
        backend = store_backend_choice(
            "maxion_continuous", selected_backend(self.backend_combo)
        )
        orig.BACKEND = backend
        run_with_console(lambda: orig.main(self.files, backend=backend), self.console)


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
    dlg.show()
    if owns:
        app.exec()


if __name__ == "__main__":
    main()

