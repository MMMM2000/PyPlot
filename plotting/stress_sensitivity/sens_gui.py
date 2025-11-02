from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.stress_sensitivity import core as orig
    from plotting.plugins.stress_sensitivity import (
        StressSensitivityPlugin as PyPlotStressSensitivityPlugin,
    )
    try:
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
            restore_backend_choice as _restore_backend_choice,
            store_backend_choice,
            selected_backend,
            restore_png_dpi,
            store_png_dpi,
        )
    except ImportError:
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
            store_backend_choice,
            selected_backend,
            restore_png_dpi,
            store_png_dpi,
        )
        _restore_backend_choice = None  # type: ignore[assignment]
else:
    from . import core as orig
    from ..plugins.stress_sensitivity import (
        StressSensitivityPlugin as PyPlotStressSensitivityPlugin,
    )
    try:
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
            restore_backend_choice as _restore_backend_choice,
            store_backend_choice,
            selected_backend,
            restore_png_dpi,
            store_png_dpi,
        )
    except ImportError:
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
            store_backend_choice,
            selected_backend,
            restore_png_dpi,
            store_png_dpi,
        )
        _restore_backend_choice = None  # type: ignore[assignment]


if "_restore_backend_choice" not in globals() or _restore_backend_choice is None:  # type: ignore[name-defined]

    def restore_backend_choice(
        key: str, combo: QtWidgets.QComboBox, default: str = "matplotlib"
    ) -> str:
        """Legacy fallback that selects ``default`` without persisting state."""

        normalised = str(default or "matplotlib").lower()
        values = [combo.itemText(idx).strip().lower() for idx in range(combo.count())]
        if not values:
            return normalised
        if normalised not in values:
            normalised = "matplotlib" if "matplotlib" in values else values[0]
        combo.setCurrentIndex(values.index(normalised))
        return normalised

else:
    restore_backend_choice = _restore_backend_choice  # type: ignore[assignment]


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stress Sensitivity Settings")

        self.files, file_widget = create_file_widget(self, key="stress_sensitivity")
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

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        orig.BACKEND = restore_backend_choice(
            "stress_sensitivity", self.backend_combo, getattr(orig, "BACKEND", "matplotlib")
        )
        self.out_dir_edit = QtWidgets.QLineEdit(get_last_output_dir(key="stress_sensitivity"))
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
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 2, 0)
        out_layout.addWidget(self.backend_combo, 2, 1)
        self.fmt_combo = QtWidgets.QComboBox()
        self.fmt_combo.addItems(["png", "pdf", "svg"])
        self.fmt_combo.setCurrentText(orig.SAVE_FORMAT)
        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 3000)
        orig.PNG_DPI = restore_png_dpi(
            "stress_sensitivity", self.dpi_spin, getattr(orig, "PNG_DPI", 1200)
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

        self.read_ctrl, read_group = create_readability_group("stress_sensitivity", orig)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(var_group, 0, 0)
        layout.addWidget(out_group, 0, 1)
        layout.addWidget(read_group, 1, 0, 1, 2)
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
            help_topic="plot_stress_sensitivity",
        )

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
        base = self.out_dir_edit.text()
        orig.OUTPUT_DIR = prepare_output_dir(base, "stress_sensitivity", self.subdir_cb.isChecked())
        set_last_output_dir(base, key="stress_sensitivity")
        sync_readability("stress_sensitivity", self.read_ctrl, orig)
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = store_png_dpi("stress_sensitivity", int(self.dpi_spin.value()))
        orig.INCLUDE_DEPENDENCE = False
        backend = store_backend_choice(
            "stress_sensitivity", selected_backend(self.backend_combo)
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

# Backwards-compatibility: expose the PyPlot plugin class from the legacy module.
StressSensitivityPlugin = PyPlotStressSensitivityPlugin


