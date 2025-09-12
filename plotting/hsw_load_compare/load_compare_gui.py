from __future__ import annotations

import sys
from PyQt6 import QtWidgets

import pathlib

if __package__ is None or __package__ == "":
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from plotting.hsw_load_compare import core as orig
    from plotting.utils import apply_system_theme, create_file_widget
else:
    from . import core as orig
    from ..utils import apply_system_theme, create_file_widget


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hsw Load Compare Settings")
        layout = QtWidgets.QGridLayout(self)

        self.files, file_widget = create_file_widget(self)
        layout.addWidget(file_widget, 0, 0, 1, 2)

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
        self.out_dir_edit = QtWidgets.QLineEdit(str(orig.OUTPUT_DIR))
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
        out_layout.addWidget(QtWidgets.QLabel("Directory:"), 3, 0)
        out_layout.addWidget(self.out_dir_edit, 4, 0)
        out_layout.addWidget(browse_btn, 4, 1)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self.run)

        layout.addWidget(plot_group, 1, 0)
        layout.addWidget(out_group, 1, 1)
        layout.addWidget(self.run_btn, 2, 0, 1, 2)

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
            "out_dir": self.out_dir_edit.text(),
            "BACKEND": ["matplotlib", "origin", "both"][self.backend_combo.currentIndex()],
        }
        orig.SAME_HIST_Y = cfg["share_y"]
        orig.SHOW_PLOTS = cfg["show"]
        orig.SAVE_PLOTS = cfg["save"]
        orig.OUTPUT_DIR = cfg["out_dir"]
        orig.main(self.files, cfg)


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

