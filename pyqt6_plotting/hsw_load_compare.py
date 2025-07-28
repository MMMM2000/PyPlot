from __future__ import annotations
import sys
from typing import List, Dict

from PyQt6 import QtWidgets

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_plotting import Hsw_load_compare as orig
from pyqt6_plotting.utils import apply_dark_theme


def ask_files() -> List[str]:
    paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
        None,
        "Select Hsw measurement files",
        "",
        "Text files (*.txt);;All files (*)",
    )
    if not paths:
        sys.exit("No files selected.")
    return list(paths)


def ask_options() -> Dict[str, object]:
    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Hsw Load Compare Settings")
    layout = QtWidgets.QGridLayout(dialog)

    tt_cb = QtWidgets.QCheckBox("Plot TT"); tt_cb.setChecked(True)
    hh_cb = QtWidgets.QCheckBox("Plot HH"); hh_cb.setChecked(True)
    raw_cb = QtWidgets.QCheckBox("Show raw"); raw_cb.setChecked(False)
    hist_cb = QtWidgets.QCheckBox("Show histograms"); hist_cb.setChecked(False)
    share_cb = QtWidgets.QCheckBox("Same hist Y"); share_cb.setChecked(orig.SAME_HIST_Y)
    show_cb = QtWidgets.QCheckBox("Show plots"); show_cb.setChecked(orig.SHOW_PLOTS)
    save_cb = QtWidgets.QCheckBox("Save plots"); save_cb.setChecked(orig.SAVE_PLOTS)
    out_dir_edit = QtWidgets.QLineEdit(str(orig.OUTPUT_DIR))
    browse_btn = QtWidgets.QPushButton("Browse")

    def browse() -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Select output directory", out_dir_edit.text())
        if d:
            out_dir_edit.setText(d)

    browse_btn.clicked.connect(browse)

    plot_group = QtWidgets.QGroupBox("Plots")
    plot_layout = QtWidgets.QVBoxLayout(plot_group)
    for w in (tt_cb, hh_cb, raw_cb, hist_cb, share_cb):
        plot_layout.addWidget(w)

    out_group = QtWidgets.QGroupBox("Output")
    out_layout = QtWidgets.QGridLayout(out_group)
    out_layout.addWidget(show_cb, 0, 0)
    out_layout.addWidget(save_cb, 1, 0)
    out_layout.addWidget(QtWidgets.QLabel("Directory:"), 2, 0)
    out_layout.addWidget(out_dir_edit, 3, 0)
    out_layout.addWidget(browse_btn, 3, 1)

    run_btn = QtWidgets.QPushButton("Run")
    run_btn.clicked.connect(dialog.accept)

    layout.addWidget(plot_group, 0, 0)
    layout.addWidget(out_group, 0, 1)
    layout.addWidget(run_btn, 1, 0, 1, 2)
    dialog.setLayout(layout)

    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        sys.exit(0)

    return {
        "TT": tt_cb.isChecked(),
        "HH": hh_cb.isChecked(),
        "raw": raw_cb.isChecked(),
        "hist": hist_cb.isChecked(),
        "share_y": share_cb.isChecked(),
        "show": show_cb.isChecked(),
        "save": save_cb.isChecked(),
        "out_dir": out_dir_edit.text(),
    }


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
    files = ask_files()
    cfg = ask_options()
    orig.ProgressDialog = ProgressDialog

    orig.SAME_HIST_Y = cfg["share_y"]
    orig.SHOW_PLOTS = cfg["show"]
    orig.SAVE_PLOTS = cfg["save"]
    orig.OUTPUT_DIR = cfg["out_dir"]

    orig.main(files, cfg)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_theme(app)
    main()
    sys.exit()
