from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.hsw_load_compare import core as orig
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
        self.setWindowTitle("Hsw Load Compare Settings")

        self.files, file_widget = create_file_widget(self)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(120)

        left = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(left)

        self.tt_cb = QtWidgets.QCheckBox("Plot TT"); self.tt_cb.setChecked(True)
        self.hh_cb = QtWidgets.QCheckBox("Plot HH"); self.hh_cb.setChecked(True)
        self.raw_cb = QtWidgets.QCheckBox("Show raw"); self.raw_cb.setChecked(False)
        self.hist_cb = QtWidgets.QCheckBox("Show histograms"); self.hist_cb.setChecked(False)
        self.share_cb = QtWidgets.QCheckBox("Same hist Y"); self.share_cb.setChecked(orig.SAME_HIST_Y)

        plot_group = QtWidgets.QGroupBox("Plots")
        plot_layout = QtWidgets.QVBoxLayout(plot_group)
        for w in (self.tt_cb, self.hh_cb, self.raw_cb, self.hist_cb, self.share_cb):
            plot_layout.addWidget(w)

        self.show_cb = QtWidgets.QCheckBox("Show plots"); self.show_cb.setChecked(orig.SHOW_PLOTS)
        self.save_cb = QtWidgets.QCheckBox("Save plots"); self.save_cb.setChecked(orig.SAVE_PLOTS)
        self.out_dir_edit = QtWidgets.QLineEdit(get_last_output_dir())
        browse_btn = QtWidgets.QPushButton("Browse")

        def browse() -> None:
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output directory", self.out_dir_edit.text())
            if d:
                self.out_dir_edit.setText(d)

        browse_btn.clicked.connect(browse)

        out_group = QtWidgets.QGroupBox("Output")
        out_layout = QtWidgets.QGridLayout(out_group)
        out_layout.addWidget(self.show_cb, 0, 0)
        out_layout.addWidget(self.save_cb, 1, 0)
        self.backend_combo = QtWidgets.QComboBox(); self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        self.backend_combo.setCurrentIndex(0)
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

        self.read_cb = QtWidgets.QCheckBox("Improve readability")
        self.read_cb.setChecked(get_readability("hsw_load_compare"))
        read_group = QtWidgets.QGroupBox("Readability")
        rl = QtWidgets.QVBoxLayout(read_group); rl.addWidget(self.read_cb)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(plot_group, 0, 0)
        layout.addWidget(out_group, 0, 1)
        layout.addWidget(read_group, 1, 0, 1, 2)
        layout.addWidget(self.run_btn, 2, 0, 1, 2)

        arrange_side_panel(self, left, file_widget, self.console)

    def run(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        cfg = {
            "TT": self.tt_cb.isChecked(),
            "HH": self.hh_cb.isChecked(),
            "raw": self.raw_cb.isChecked(),
            "hist": self.hist_cb.isChecked(),
            "share_y": self.share_cb.isChecked(),
            "show": self.show_cb.isChecked(),
            "save": self.save_cb.isChecked(),
            "out_dir": prepare_output_dir(self.out_dir_edit.text(), "hsw_load_compare", self.subdir_cb.isChecked()),
            "BACKEND": ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()],
        }
        orig.SAVE_FORMAT = self.fmt_combo.currentText()
        orig.PNG_DPI = int(self.dpi_spin.value())
        orig.SAME_HIST_Y = cfg["share_y"]
        orig.SHOW_PLOTS = cfg["show"]
        orig.SAVE_PLOTS = cfg["save"]
        orig.OUTPUT_DIR = cfg["out_dir"]
        orig.IMPROVE_READABILITY = self.read_cb.isChecked()
        set_readability("hsw_load_compare", orig.IMPROVE_READABILITY)
        set_last_output_dir(self.out_dir_edit.text())
        run_with_console(lambda: orig.main(self.files, cfg), self.console)


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

