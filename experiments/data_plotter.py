from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, Dict, Tuple, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.config import load_config
from plotting.utils import apply_system_theme


def _discover_modules() -> Dict[str, Tuple[Any, str]]:
    modules: Dict[str, Tuple[Any, str]] = {}
    pkg_path = ROOT / "plotting"
    for pkg in pkgutil.iter_modules([str(pkg_path)]):
        if not pkg.ispkg:
            continue
        mod: Any | None = None
        cfg_key = pkg.name
        # try core.py first
        try:
            mod = importlib.import_module(f"plotting.{pkg.name}.core")
        except Exception:
            # search for any *_gui module
            spec = importlib.util.find_spec(f"plotting.{pkg.name}")
            if spec and spec.submodule_search_locations:
                for sub in pkgutil.iter_modules(spec.submodule_search_locations):
                    if sub.name.endswith("_gui"):
                        mod = importlib.import_module(
                            f"plotting.{pkg.name}.{sub.name}"
                        )
                        break
        if mod is None or not hasattr(mod, "main"):
            continue
        pretty = pkg.name.replace("_", " ").title()
        modules[pretty] = (mod, cfg_key)
    return modules


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
        layout.addWidget(self.combo)

        file_layout = QtWidgets.QHBoxLayout()
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.file_list.itemDoubleClicked.connect(self.open_file)
        file_layout.addWidget(self.file_list, 1)

        btn_layout = QtWidgets.QVBoxLayout()
        add_files = QtWidgets.QPushButton("Add Files")
        add_files.clicked.connect(self.select_files)
        btn_layout.addWidget(add_files)
        add_folder = QtWidgets.QPushButton("Add Folder")
        add_folder.clicked.connect(self.select_folder)
        btn_layout.addWidget(add_folder)
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

    def _add_files(self, files: List[str]) -> None:
        for f in files:
            if f not in self.files:
                self.files.append(f)
                self.file_list.addItem(f)

    def select_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Select data files")
        if files:
            self._add_files(files)

    def select_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder")
        if folder:
            paths = [str(p) for p in Path(folder).iterdir() if p.is_file()]
            self._add_files(sorted(paths))

    def remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.files.remove(item.text())
            self.file_list.takeItem(self.file_list.row(item))

    def open_file(self, item: QtWidgets.QListWidgetItem) -> None:  # pragma: no cover - OS dependent
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
        try:
            df = module.load_data(self.files)
        except AttributeError:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.warning(
                self, "Unsupported", "This plotter does not support outlier checks."
            )
            return
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            return
        progress = QtWidgets.QProgressDialog(
            "Checking for outliers...", "Cancel", 0, len(df), self
        )
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        out_df = self.detect_outliers(df, progress)
        progress.close()
        if progress.wasCanceled():  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.information(self, "Outlier check", "Cancelled.")
            return
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

    def detect_outliers(
        self,
        df: "pd.DataFrame",
        progress: QtWidgets.QProgressDialog | None = None,
    ) -> "pd.DataFrame":
        import numpy as np
        import pandas as pd

        column = "sum"
        quantile = 0.9
        factor = 3.0
        out_rows = []
        low_q = (1 - quantile) / 2
        high_q = 1 - low_q
        count = 0
        for fname, grp in df.groupby("filename"):
            sub = grp[[column]].dropna().reset_index()
            values = sub[column].to_numpy()
            for idx, val in enumerate(values):
                start = max(0, idx - 10)
                end = min(values.size, idx + 11)
                window = values[start:end]
                med = np.median(window)
                q_low = np.quantile(window, low_q)
                q_high = np.quantile(window, high_q)
                rng = q_high - q_low
                if rng > 0 and abs(val - med) > factor * rng:
                    out_rows.append(grp.loc[[sub["index"].iloc[idx]]])
                count += 1
                if progress:
                    progress.setValue(count)
                    QtWidgets.QApplication.processEvents()
                    if progress.wasCanceled():
                        return pd.DataFrame(columns=df.columns)
        return pd.concat(out_rows, ignore_index=False) if out_rows else pd.DataFrame(columns=df.columns)


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
