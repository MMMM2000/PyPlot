from __future__ import annotations

import sys
import inspect
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Tuple, cast

# Ensure the repository root is on ``sys.path`` when run as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6 import QtWidgets

from plotting.config import load_config
from plotting.temperature_sensitivity.core import detect_outliers
from plotting.utils import apply_system_theme


def _discover_modules() -> Dict[str, Tuple[Any, str]]:
    modules: Dict[str, Tuple[Any, str]] = {}
    pkg_dir = ROOT / "plotting"
    for item in pkg_dir.iterdir():
        core_file = item / "core.py"
        if not core_file.is_file():
            continue
        mod_name = item.name
        mod = import_module(f"plotting.{mod_name}.core")
        main = getattr(mod, "main", None)
        if main is None:
            continue
        sig = inspect.signature(main)
        # Only include modules whose ``main`` accepts a single ``files`` argument
        if len(sig.parameters) != 1:
            continue
        label = mod_name.replace("_", " ").title()
        modules[label] = (mod, mod_name)
    return dict(sorted(modules.items()))


MODULES: Dict[str, Tuple[Any, str]] = _discover_modules()


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
        self.combo.currentTextChanged.connect(self._update_outlier_btn)
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
        self._update_outlier_btn(self.combo.currentText())

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
            elif isinstance(widget, QtWidgets.QLineEdit):
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
        if not hasattr(module, "load_data"):
            QtWidgets.QMessageBox.information(
                self,
                "Unsupported",
                "Outlier detection not available for this plotter.",
            )
            return
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

    def _update_outlier_btn(self, name: str) -> None:
        module, _ = MODULES[name]
        self.outlier_btn.setEnabled(hasattr(module, "load_data"))

    def run_plotter(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        module, _ = MODULES[self.combo.currentText()]
        cfg = self.gather_config()
        self.apply_config(module, cfg)
        try:
            module.main(self.files)
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))


def main() -> None:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication([])
        owns_app = True
    else:
        app = cast(QtWidgets.QApplication, app)
    apply_system_theme(app)
    dlg = DataPlotter()
    dlg.show()
    if owns_app:
        app.exec()


if __name__ == "__main__":
    main()
