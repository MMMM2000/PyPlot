from __future__ import annotations

import inspect
import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd
from PyQt6 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plotting.config import load_config
from plotting.temperature_sensitivity.core import detect_outliers
from plotting.utils import apply_system_theme, select_files_or_folder


def discover_modules() -> Dict[str, Tuple[Any, str]]:
    """Dynamically discover plotting modules.

    Any package inside ``plotting`` containing either a ``core`` module or a
    ``*_gui`` module is loaded.  The package name is title‑cased for display and
    also used as the configuration key.
    """

    mods: Dict[str, Tuple[Any, str]] = {}
    pkg_path = ROOT / "plotting"
    for info in pkgutil.iter_modules([str(pkg_path)]):
        if not info.ispkg:
            continue
        cfg_key = info.name
        title = cfg_key.replace("_", " ").title()
        module = None
        for sub in ("core", f"{cfg_key}_gui"):
            try:
                module = importlib.import_module(f"plotting.{cfg_key}.{sub}")
                break
            except Exception:  # pragma: no cover - best effort import
                continue
        if module and hasattr(module, "main"):
            mods[title] = (module, cfg_key)
    return mods


MODULES: Dict[str, Tuple[Any, str]] = discover_modules()


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
        self.file_list.itemDoubleClicked.connect(self.open_file)
        file_layout.addWidget(self.file_list, 1)

        btn_layout = QtWidgets.QVBoxLayout()
        select_btn = QtWidgets.QPushButton("Add Files/Folders")
        select_btn.clicked.connect(self.select_files)
        btn_layout.addWidget(select_btn)
        remove_btn = QtWidgets.QPushButton("Remove Selected")
        remove_btn.clicked.connect(self.remove_selected)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch(1)
        file_layout.addLayout(btn_layout)
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
        files = select_files_or_folder(self)
        if not files:
            return
        new = [f for f in files if f not in self.files]
        self.files.extend(new)
        self.file_list.addItems(new)

    def remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
            try:
                self.files.remove(item.text())
            except ValueError:
                pass

    def open_file(self, item: QtWidgets.QListWidgetItem) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(item.text()))

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
        if not hasattr(module, "load_data"):
            QtWidgets.QMessageBox.information(
                self, "Unsupported", "Outlier detection not available for this plotter."
            )
            return
        try:
            df = module.load_data(self.files)
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            return
        total = df["filename"].nunique()
        progress = QtWidgets.QProgressDialog(
            "Checking for outliers...", "Cancel", 0, total, self
        )
        progress.setWindowTitle("Outlier Check")
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)

        parts = []
        for i, (_fname, grp) in enumerate(df.groupby("filename"), 1):
            if progress.wasCanceled():
                break
            parts.append(detect_outliers(grp))
            progress.setValue(i)
            QtWidgets.QApplication.processEvents()
        progress.close()
        out_df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
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
            params = len(inspect.signature(module.main).parameters)
            if params == 0:
                module.main()
            elif params == 1:
                module.main(self.files)
            else:
                module.main(self.files, cfg)
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
