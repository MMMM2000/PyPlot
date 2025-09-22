from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.current_annealing import core as orig
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
        restore_combo_choice,
        store_combo_choice,
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
        restore_combo_choice,
        store_combo_choice,
    )


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Current Annealing Plot Settings")

        self.files, file_widget = create_file_widget(self, key="current_annealing")
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)

        left = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(left)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.out_dir_edit = QtWidgets.QLineEdit(get_last_output_dir(key="current_annealing"))
        browse_btn = QtWidgets.QPushButton("Browse")
        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        orig.BACKEND = restore_backend_choice(
            "current_annealing", self.backend_combo, getattr(orig, "BACKEND", "matplotlib")
        )
        self.origin_mode_label = QtWidgets.QLabel("Origin style:")
        self.origin_mode_combo = QtWidgets.QComboBox()
        self.origin_mode_combo.addItem(
            "Experimental (directional)", getattr(orig, "ORIGIN_MODES", ["experimental"])[0]
        )
        self.origin_mode_combo.addItem(
            "Simple (single trace)", getattr(orig, "ORIGIN_MODES", ["experimental", "simple"])[-1]
        )
        orig.ORIGIN_MODE = restore_combo_choice(
            "current_annealing",
            "origin_mode",
            self.origin_mode_combo,
            getattr(orig, "ORIGIN_MODE", getattr(orig, "ORIGIN_MODES", ["experimental"])[0]),
        )
        normaliser = getattr(orig, "_normalise_origin_mode", lambda value: value)
        orig.ORIGIN_MODE = normaliser(orig.ORIGIN_MODE)

        def browse() -> None:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir_edit.text())
            if d:
                self.out_dir_edit.setText(d)

        browse_btn.clicked.connect(browse)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        out_layout.addWidget(self.show_cb, 0, 0)
        out_layout.addWidget(self.save_cb, 1, 0)
        out_layout.addWidget(QtWidgets.QLabel("Backend:"), 2, 0)
        out_layout.addWidget(self.backend_combo, 2, 1)
        out_layout.addWidget(self.origin_mode_label, 3, 0)
        out_layout.addWidget(self.origin_mode_combo, 3, 1)
        self.fmt_combo = QtWidgets.QComboBox()
        self.fmt_combo.addItems(["png", "pdf", "svg"])
        self.fmt_combo.setCurrentText(orig.SAVE_FORMAT)
        self.dpi_spin = QtWidgets.QSpinBox()
        self.dpi_spin.setRange(72, 3000)
        orig.PNG_DPI = restore_png_dpi(
            "current_annealing", self.dpi_spin, getattr(orig, "PNG_DPI", 1200)
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

        self.read_ctrl, read_group = create_readability_group("current_annealing", orig)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(out_group)
        layout.addWidget(read_group)
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
            help_topic="plot_current_annealing",
        )
        self.backend_combo.currentIndexChanged.connect(self._update_origin_mode_state)
        self._update_origin_mode_state()

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        orig.SHOW_PLOTS = self.show_cb.isChecked()
        orig.SAVE_PLOTS = self.save_cb.isChecked()
        base = self.out_dir_edit.text()
        orig.OUTPUT_DIR = prepare_output_dir(base, "current_annealing", self.subdir_cb.isChecked())
        set_last_output_dir(base, key="current_annealing")
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = store_png_dpi(
            "current_annealing", int(self.dpi_spin.value())
        )
        sync_readability("current_annealing", self.read_ctrl, orig)
        backend = store_backend_choice(
            "current_annealing", selected_backend(self.backend_combo)
        )
        orig.BACKEND = backend
        orig.ORIGIN_MODE = store_combo_choice(
            "current_annealing", "origin_mode", self.origin_mode_combo
        )
        normaliser = getattr(orig, "_normalise_origin_mode", lambda value: value)
        orig.ORIGIN_MODE = normaliser(orig.ORIGIN_MODE)
        run_with_console(lambda: orig.main(self.files, backend=backend), self.console)

    def _update_origin_mode_state(self) -> None:
        backend = selected_backend(self.backend_combo)
        has_origin = backend in ("origin", "both")
        self.origin_mode_combo.setEnabled(has_origin)
        self.origin_mode_label.setEnabled(has_origin)


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        ensure_app_theme(app)
        owns = True
    dlg = SettingsDialog()
    dlg.show()
    if owns:
        app.exec()


if __name__ == "__main__":
    main()

