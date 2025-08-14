"""Experimental master data plotter GUI."""
from __future__ import annotations

import json
import sys
from typing import Callable, List, NamedTuple

import pandas as pd
from PyQt6 import QtWidgets

from plotting.stress_dependence import stress_gui, core as stress_core
from plotting.hsw_load_compare import load_compare_gui, core as load_compare_core
from plotting.maxion_continuous import maxion_gui, core as maxion_core
from plotting.hsw_distribution import distribution_gui, core as distribution_core
from plotting.temperature_sensitivity import temp_gui, core as temp_core
from plotting.temperature_dependence import temp_dep_gui, core as temp_dep_core
from plotting.stress_sensitivity import sens_gui, core as sens_core
from plotting.hysteresis_loops import loops_gui, core as loops_core
from plotting.config import load_config
from plotting.temperature_sensitivity.core import detect_outliers
from plotting import common


class PlotterInfo(NamedTuple):
    main: Callable[[List[str]], None]
    core: object
    load_data: Callable[[List[str]], pd.DataFrame]


PLOTTERS = {
    "Stress Dependence": PlotterInfo(stress_gui.main, stress_core, stress_core.load_data),
    "Load Compare": PlotterInfo(load_compare_gui.main, load_compare_core, load_compare_core.load_data),
    "Maxion Continuous": PlotterInfo(maxion_gui.main, maxion_core, maxion_core.load_data),
    "HSW Distribution": PlotterInfo(distribution_gui.main, distribution_core, distribution_core.load_data),
    "Temperature Sensitivity": PlotterInfo(temp_gui.main, temp_core, temp_core.load_data),
    "Temperature Dependence": PlotterInfo(temp_dep_gui.main, temp_dep_core, temp_dep_core.load_data),
    "Stress Sensitivity": PlotterInfo(sens_gui.main, sens_core, sens_core.load_data),
    "Hysteresis Loops": PlotterInfo(loops_gui.main, loops_core, loops_core.load_data),
}


class DataPlotter(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Data Plotter (Experiment)")
        self.files: List[str] = []

        layout = QtWidgets.QVBoxLayout(self)

        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(PLOTTERS.keys())
        self.combo.currentIndexChanged.connect(self.load_config)
        layout.addWidget(self.combo)

        file_btn = QtWidgets.QPushButton("Select files")
        file_btn.clicked.connect(self.select_files)
        layout.addWidget(file_btn)

        self.files_label = QtWidgets.QLabel("No files selected")
        layout.addWidget(self.files_label)

        self.config_edit = QtWidgets.QPlainTextEdit()
        self.config_edit.setPlaceholderText("Config (JSON)")
        layout.addWidget(self.config_edit)

        opts_layout = QtWidgets.QHBoxLayout()
        self.show_cb = QtWidgets.QCheckBox("Show plots")
        self.show_cb.setChecked(True)
        opts_layout.addWidget(self.show_cb)
        self.save_cb = QtWidgets.QCheckBox("Save plots")
        opts_layout.addWidget(self.save_cb)
        layout.addLayout(opts_layout)

        check_btn = QtWidgets.QPushButton("Check outliers")
        check_btn.clicked.connect(self.check_outliers)
        layout.addWidget(check_btn)

        plot_btn = QtWidgets.QPushButton("Plot")
        plot_btn.clicked.connect(self.plot_data)
        layout.addWidget(plot_btn)

        self.load_config()

    def select_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Select data files")
        if files:
            self.files = files
            self.files_label.setText(f"{len(files)} files selected")
        else:
            self.files_label.setText("No files selected")

    def load_config(self) -> None:
        info = PLOTTERS[self.combo.currentText()]
        key = info.core.__package__.split('.')[-1]
        cfg = load_config().get(key, {})
        self.config_edit.setPlainText(json.dumps(cfg, indent=2))

    def apply_config(self, info: PlotterInfo) -> None:
        try:
            cfg = json.loads(self.config_edit.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid config", str(exc))
            raise
        for key, val in cfg.items():
            if hasattr(info.core, key):
                setattr(info.core, key, val)
        if hasattr(info.core, "SHOW_PLOTS"):
            setattr(info.core, "SHOW_PLOTS", self.show_cb.isChecked())
        if hasattr(info.core, "SAVE_PLOTS"):
            setattr(info.core, "SAVE_PLOTS", self.save_cb.isChecked())

    def check_outliers(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.information(self, "No files", "Select data files first.")
            return
        info = PLOTTERS[self.combo.currentText()]
        try:
            df = info.load_data(self.files)
            out = detect_outliers(df)
        except Exception as exc:  # pragma: no cover - gui feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            return
        if out.empty:
            QtWidgets.QMessageBox.information(self, "Outliers", "No outliers detected.")
        else:
            files = ", ".join(sorted(out["filename"].unique()))
            QtWidgets.QMessageBox.warning(self, "Outliers", f"Outliers detected in: {files}")

    def plot_data(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.information(self, "No files", "Select data files first.")
            return
        info = PLOTTERS[self.combo.currentText()]
        try:
            self.apply_config(info)
            common.CHECK_OUTLIERS = False
            info.main(self.files)
        except Exception as exc:  # pragma: no cover - gui feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = DataPlotter()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
