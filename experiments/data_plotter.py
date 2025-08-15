from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6 import QtWidgets

from plotting.config import load_config
from plotting.temperature_sensitivity.core import detect_outliers
from plotting.utils import apply_system_theme
from plotting.stress_dependence import core as stress_core
from plotting.stress_sensitivity import core as sens_core
from plotting.temperature_sensitivity import core as temp_core
from plotting.temperature_dependence import core as temp_dep_core
from plotting.hsw_load_compare import core as load_core
from plotting.hysteresis_loops import core as loops_core
from plotting.maxion_continuous import core as maxion_core

MODULES: Dict[str, Tuple[Any, str]] = {
    "Stress Dependence": (stress_core, "stress_dependence"),
    "Stress Sensitivity": (sens_core, "stress_sensitivity"),
    "Temperature Sensitivity": (temp_core, "temperature_sensitivity"),
    "Temperature Dependence": (temp_dep_core, "temperature_dependence"),
    "HSW Load Compare": (load_core, "hsw_load_compare"),
    "Hysteresis Loops": (loops_core, "hysteresis_loops"),
    "Maxion Continuous": (maxion_core, "maxion_continuous"),
}


class DataPlotter(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Data Plotter (Experimental)")
        self.files: list[str] = []
        self.cfg_widgets: Dict[str, QtWidgets.QWidget] = {}

        layout = QtWidgets.QVBoxLayout(self)

        self.combo = QtWidgets.QComboBox()
        self.combo.addItems(MODULES.keys())
        self.combo.currentTextChanged.connect(self.populate_settings)
        layout.addWidget(self.combo)

        file_layout = QtWidgets.QHBoxLayout()
        self.file_list = QtWidgets.QListWidget()
        file_layout.addWidget(self.file_list, 1)
        select_btn = QtWidgets.QPushButton("Select Files")
        select_btn.clicked.connect(self.select_files)
        file_layout.addWidget(select_btn)
        layout.addLayout(file_layout)

        self.settings_group = QtWidgets.QGroupBox("Settings")
        self.settings_layout = QtWidgets.QFormLayout(self.settings_group)
        layout.addWidget(self.settings_group)

        self.outlier_btn = QtWidgets.QPushButton("Check Outliers")
        self.outlier_btn.clicked.connect(self.check_outliers)
        layout.addWidget(self.outlier_btn)

        run_btn = QtWidgets.QPushButton("Plot")
        run_btn.clicked.connect(self.run_plotter)
        layout.addWidget(run_btn)

        self.cfg = load_config()
        self.populate_settings(self.combo.currentText())

    def populate_settings(self, name: str) -> None:
        for i in reversed(range(self.settings_layout.count())):
            item = self.settings_layout.takeAt(i)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.cfg_widgets.clear()
        _, cfg_key = MODULES[name]
        for key, value in self.cfg.get(cfg_key, {}).items():
            if isinstance(value, bool):
                widget: QtWidgets.QWidget = QtWidgets.QCheckBox()
                widget.setChecked(value)
            else:
                widget = QtWidgets.QLineEdit(str(value))
            self.settings_layout.addRow(key, widget)
            self.cfg_widgets[key] = widget

    def select_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Select data files")
        if files:
            self.files = files
            self.file_list.clear()
            self.file_list.addItems(files)

    def gather_config(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {}
        for key, widget in self.cfg_widgets.items():
            if isinstance(widget, QtWidgets.QCheckBox):
                cfg[key] = widget.isChecked()
            else:
                text = widget.text()
                try:
                    cfg[key] = int(text)
                except ValueError:
                    try:
                        cfg[key] = float(text)
                    except ValueError:
                        cfg[key] = text
        return cfg

    def apply_config(self, module: Any, cfg: Dict[str, Any]) -> None:
        for k, v in cfg.items():
            setattr(module, k, v)
        if hasattr(module, "PLOT_VARS"):
            module.PLOT_VARS = [
                v for v in ("sum", "dT", "T1", "T2") if getattr(module, f"PLOT_{v.upper()}", False)
            ]

    def check_outliers(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        module, _ = MODULES[self.combo.currentText()]
        try:
            df = module.load_data(self.files)
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            return
        out_df = detect_outliers(df)
        QtWidgets.QMessageBox.information(
            self,
            "Outlier check",
            f"{len(out_df)} outliers detected.",
        )

    def run_plotter(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        module, _ = MODULES[self.combo.currentText()]
        cfg = self.gather_config()
        self.apply_config(module, cfg)
        try:
            if len(inspect.signature(module.main).parameters) > 1:
                module.main(self.files, cfg)
            else:
                module.main(self.files)
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        owns_app = True
    apply_system_theme(app)
    dlg = DataPlotter()
    dlg.show()
    if owns_app:
        app.exec()


if __name__ == "__main__":
    main()
