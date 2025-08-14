from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from PyQt6 import QtWidgets

from plotting.hsw_distribution import distribution_gui
from plotting.hsw_load_compare import load_compare_gui
from plotting.hysteresis_loops import loops_gui
from plotting.maxion_continuous import maxion_gui
from plotting.pdf_plotter import pdf_gui
from plotting.stress_dependence import stress_gui
from plotting.stress_sensitivity import sens_gui
from plotting.temperature_dependence import temp_dep_gui
from plotting.temperature_sensitivity import temp_gui
from plotting.temperature_sensitivity.core import detect_outliers
from plotting.utils import apply_system_theme

PLOTTERS = {
    "Stress Dependence": stress_gui.main,
    "Hsw Load Compare": load_compare_gui.main,
    "Maxion Continuous": maxion_gui.main,
    "Hsw Distribution": distribution_gui.main,
    "Temperature Sensitivity": temp_gui.main,
    "Temperature Dependence": temp_dep_gui.main,
    "Stress Sensitivity": sens_gui.main,
    "PDF Plotter": pdf_gui.main,
    "Hysteresis Loops": loops_gui.main,
}


class DataPlotter(QtWidgets.QDialog):
    """Simple experiment to launch plotting scripts with selected data."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Data Plotter (Experiment)")
        layout = QtWidgets.QVBoxLayout(self)

        self.plot_list = QtWidgets.QListWidget()
        for name in PLOTTERS:
            self.plot_list.addItem(name)
        self.plot_list.setCurrentRow(0)
        layout.addWidget(self.plot_list)

        file_layout = QtWidgets.QHBoxLayout()
        self.file_edit = QtWidgets.QLineEdit()
        file_layout.addWidget(self.file_edit)
        file_btn = QtWidgets.QPushButton("Select Data")
        file_btn.clicked.connect(self.select_data)
        file_layout.addWidget(file_btn)
        layout.addLayout(file_layout)

        self.outlier_btn = QtWidgets.QPushButton("Check Outliers")
        self.outlier_btn.clicked.connect(self.check_outliers)
        layout.addWidget(self.outlier_btn)

        self.plot_btn = QtWidgets.QPushButton("Plot")
        self.plot_btn.clicked.connect(self.run_selected)
        layout.addWidget(self.plot_btn)

        self.data_files: list[str] = []

    def select_data(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Select Data Files")
        if files:
            self.data_files = files
            self.file_edit.setText("; ".join(files))

    def check_outliers(self) -> None:
        if not self.data_files:
            QtWidgets.QMessageBox.warning(self, "No data", "Please select data files first")
            return
        dfs = []
        for path in self.data_files:
            try:
                df = pd.read_csv(path, sep=None, engine="python")
                df["filename"] = Path(path).name
                if "line" not in df.columns:
                    df["line"] = range(len(df))
                dfs.append(df)
            except Exception as exc:  # pragma: no cover - experimental UI
                QtWidgets.QMessageBox.critical(self, "Error", f"Could not read {path}: {exc}")
                return
        data = pd.concat(dfs, ignore_index=True)
        out_df = detect_outliers(data)
        QtWidgets.QMessageBox.information(
            self,
            "Outlier check",
            f"Found {len(out_df)} outliers",
        )

    def run_selected(self) -> None:
        item = self.plot_list.currentItem()
        if item is None:
            QtWidgets.QMessageBox.warning(self, "No selection", "Please select a plotting script")
            return
        func = PLOTTERS[item.text()]
        func()


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True
    apply_system_theme(app)
    dlg = DataPlotter()
    dlg.show()
    if owns_app:
        app.exec()


if __name__ == "__main__":
    main()
