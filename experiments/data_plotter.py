from __future__ import annotations


import json
from pathlib import Path
from typing import Callable, Dict, List, Tuple, Any

import pandas as pd
from PyQt6 import QtWidgets

from plotting.hsw_distribution import distribution_gui, core as distribution_core
from plotting.hsw_load_compare import load_compare_gui, core as load_compare_core
from plotting.hysteresis_loops import loops_gui, core as loops_core
from plotting.maxion_continuous import maxion_gui, core as maxion_core
from plotting.pdf_plotter import pdf_gui, core as pdf_core
from plotting.stress_dependence import stress_gui, core as stress_core
from plotting.stress_sensitivity import sens_gui, core as sens_core
from plotting.temperature_dependence import temp_dep_gui, core as temp_dep_core
from plotting.temperature_sensitivity import temp_gui, core as temp_core
from plotting.temperature_sensitivity import core as ts_core
from plotting.utils import apply_system_theme

PLOTTERS: Dict[str, Tuple[Callable[[], QtWidgets.QWidget | None], Any]] = {
    "Stress Dependence": (stress_gui.main, stress_core),
    "Hsw Load Compare": (load_compare_gui.main, load_compare_core),
    "Maxion Continuous": (maxion_gui.main, maxion_core),
    "Hsw Distribution": (distribution_gui.main, distribution_core),
    "Temperature Sensitivity": (temp_gui.main, temp_core),
    "Temperature Dependence": (temp_dep_gui.main, temp_dep_core),
    "Stress Sensitivity": (sens_gui.main, sens_core),
    "PDF Plotter": (pdf_gui.main, pdf_core),
    "Hysteresis Loops": (loops_gui.main, loops_core),
}


class DataPlotter(QtWidgets.QWidget):
    """Experimental master plotter GUI."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Experimental Data Plotter")
        layout = QtWidgets.QVBoxLayout(self)

        self.plot_select = QtWidgets.QComboBox()
        self.plot_select.addItems(PLOTTERS.keys())
        self.plot_select.currentTextChanged.connect(self.update_config)
        layout.addWidget(self.plot_select)

        file_layout = QtWidgets.QHBoxLayout()
        self.file_edit = QtWidgets.QLineEdit()
        self.file_edit.setReadOnly(True)
        browse_btn = QtWidgets.QPushButton("Select Data…")
        browse_btn.clicked.connect(self.select_files)
        file_layout.addWidget(self.file_edit)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)

        self.config_edit = QtWidgets.QPlainTextEdit()
        self.config_edit.setPlaceholderText("Configuration JSON")
        layout.addWidget(self.config_edit)

        self.outlier_btn = QtWidgets.QPushButton("Check Outliers")
        self.outlier_btn.setEnabled(False)
        self.outlier_btn.clicked.connect(self.check_outliers)
        layout.addWidget(self.outlier_btn)

        btn_layout = QtWidgets.QHBoxLayout()
        plot_btn = QtWidgets.QPushButton("Plot")
        plot_btn.clicked.connect(self.plot)
        save_btn = QtWidgets.QPushButton("Plot && Save")
        save_btn.clicked.connect(lambda: self.plot(save=True))
        btn_layout.addWidget(plot_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        self.files: List[str] = []
        self.update_config()

    def select_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select data files", "", "Text Files (*.txt);;All Files (*)"
        )
        if files:
            self.files = files
            names = "; ".join(Path(f).name for f in files)
            self.file_edit.setText(names)
            self.outlier_btn.setEnabled(True)

    def update_config(self) -> None:
        _, core_mod = PLOTTERS[self.plot_select.currentText()]
        try:
            cfg_text = json.dumps(getattr(core_mod, "_CFG", {}), indent=2)
        except TypeError:
            cfg_text = "{}"
        self.config_edit.setPlainText(cfg_text)

    def check_outliers(self) -> None:
        if not self.files:
            return
        dfs = []
        for fn in self.files:
            series = pd.read_csv(
                fn,
                sep=";",
                header=None,
                names=["sum"],
                engine="python",
                on_bad_lines="skip",
            )["sum"]
            df = pd.DataFrame({"sum": series, "filename": Path(fn).name, "line": range(len(series))})
            dfs.append(df)
        data = pd.concat(dfs, ignore_index=True)
        out = ts_core.detect_outliers(data)
        msg = f"Detected {len(out)} outliers" if not out.empty else "No outliers detected"
        QtWidgets.QMessageBox.information(self, "Outlier check", msg)

    def plot(self, save: bool = False) -> None:
        func, core_mod = PLOTTERS[self.plot_select.currentText()]
        try:
            new_cfg = json.loads(self.config_edit.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            QtWidgets.QMessageBox.critical(self, "Invalid config", str(exc))
            return
        if hasattr(core_mod, "_CFG") and isinstance(getattr(core_mod, "_CFG"), dict):
            core_mod._CFG.update(new_cfg)
        if save and hasattr(core_mod, "SAVE_PLOTS"):
            core_mod.SAVE_PLOTS = True
        try:
            func()
        except Exception as exc:  # pragma: no cover - unexpected errors
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))


def main() -> None:
    app = QtWidgets.QApplication([])
    apply_system_theme(app)
    w = DataPlotter()
    w.show()
    app.exec()


if __name__ == "__main__":
    main()
