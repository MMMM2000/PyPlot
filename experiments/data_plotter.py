from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, Dict, Tuple, cast

from PyQt6 import QtWidgets

sys.path.append(str(Path(__file__).resolve().parents[1]))

from plotting.config import load_config  # noqa: E402
from plotting.temperature_sensitivity.core import detect_outliers  # noqa: E402
from plotting.utils import apply_system_theme  # noqa: E402


def _discover_modules() -> Dict[str, Tuple[Any, str]]:
    modules: Dict[str, Tuple[Any, str]] = {}
    base = Path(__file__).resolve().parents[1] / "plotting"
    for mod in pkgutil.iter_modules([str(base)]):
        try:
            core = importlib.import_module(f"plotting.{mod.name}.core")
        except ModuleNotFoundError:
            continue
        if hasattr(core, "main"):
            label = mod.name.replace("_", " ").title()
            modules[label] = (core, mod.name)
    return modules


MODULES: Dict[str, Tuple[Any, str]] = _discover_modules()


class DataPlotter(QtWidgets.QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Data Plotter (Experimental)")
        self.files: list[str] = []
        self.cfg_widgets: Dict[str, QtWidgets.QCheckBox | QtWidgets.QLineEdit] = {}

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
            if item is not None:
                w = item.widget()
                if w is not None:
                    w.deleteLater()
        self.cfg_widgets.clear()
        _, cfg_key = MODULES[name]
        for key, value in self.cfg.get(cfg_key, {}).items():
            if isinstance(value, bool):
                cb = QtWidgets.QCheckBox()
                cb.setChecked(value)
                widget: QtWidgets.QCheckBox | QtWidgets.QLineEdit = cb
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
                assert isinstance(widget, QtWidgets.QLineEdit)
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
    apply_system_theme(cast(QtWidgets.QApplication, app))
    dlg = DataPlotter()
    dlg.show()
    if owns_app:
        app.exec()


if __name__ == "__main__":
    main()
