from __future__ import annotations

import inspect
import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6 import QtCore, QtGui, QtWidgets

from plotting.config import load_config
from plotting.temperature_sensitivity.core import detect_outliers
from plotting.utils import apply_system_theme, select_files_or_folder


def _discover_modules() -> Dict[str, Tuple[Any, str]]:
    """Return available plotting modules in the :mod:`plotting` package."""

    modules: Dict[str, Tuple[Any, str]] = {}
    pkg = importlib.import_module("plotting")
    for info in pkgutil.iter_modules(pkg.__path__):
        name = info.name
        if name in {"common", "config", "utils"}:
            continue
        submod = None
        try:
            submod = importlib.import_module(f"plotting.{name}.core")
        except ModuleNotFoundError:
            try:
                package = importlib.import_module(f"plotting.{name}")
            except ModuleNotFoundError:
                continue
            for sub in pkgutil.iter_modules(package.__path__):
                if sub.name.endswith("_gui"):
                    submod = importlib.import_module(
                        f"plotting.{name}.{sub.name}"
                    )
                    break
        if submod is None or not hasattr(submod, "main"):
            continue
        pretty = name.replace("_", " ").title()
        modules[pretty] = (submod, name)
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
        self.combo.currentTextChanged.connect(self.on_module_change)
        layout.addWidget(self.combo)

        file_layout = QtWidgets.QHBoxLayout()
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.file_list.itemDoubleClicked.connect(self.open_file)
        file_layout.addWidget(self.file_list, 1)
        btn_layout = QtWidgets.QVBoxLayout()
        select_btn = QtWidgets.QPushButton("Add Files/Folders")
        select_btn.clicked.connect(self.select_files)
        btn_layout.addWidget(select_btn)
        remove_btn = QtWidgets.QPushButton("Remove Selected")
        remove_btn.clicked.connect(self.remove_selected)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()
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
        self.on_module_change(self.combo.currentText())

    def on_module_change(self, name: str) -> None:
        self.populate_settings(name)
        self.update_outlier_button(name)

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

    def update_outlier_button(self, name: str) -> None:
        module, _ = MODULES[name]
        self.outlier_btn.setEnabled(hasattr(module, "load_data"))

    def select_files(self) -> None:
        module, _ = MODULES[self.combo.currentText()]
        ext = getattr(module, "FILE_EXT", ".txt")
        files = select_files_or_folder(self, ext=ext)
        if files:
            for f in files:
                if f not in self.files:
                    self.files.append(f)
            self.file_list.clear()
            self.file_list.addItems(self.files)

    def remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            self.files.remove(item.text())
            row = self.file_list.row(item)
            self.file_list.takeItem(row)

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
                self, "Not supported", "Outlier detection not available for this module."
            )
            return
        try:
            df = module.load_data(self.files)
        except Exception as exc:  # pragma: no cover - GUI feedback
            QtWidgets.QMessageBox.critical(self, "Error", str(exc))
            return

        progress = QtWidgets.QProgressDialog(
            "Checking outliers...", "Cancel", 0, len(df), self
        )
        progress.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        progress.show()

        def update(val: int, total: int) -> None:
            if progress.wasCanceled():
                raise KeyboardInterrupt
            progress.setMaximum(total)
            progress.setValue(val)
            QtWidgets.QApplication.processEvents()

        try:
            out_df = detect_outliers(df, progress=update)
        except KeyboardInterrupt:
            progress.close()
            return
        progress.close()
        QtWidgets.QMessageBox.information(
            self, "Outlier check", f"{len(out_df)} outliers detected."
        )

    def run_plotter(self) -> None:
        if not self.files:
            QtWidgets.QMessageBox.warning(self, "No files", "Select files first.")
            return
        module, _ = MODULES[self.combo.currentText()]
        cfg = self.gather_config()
        self.apply_config(module, cfg)
        try:
            sig = inspect.signature(module.main)
            if len(sig.parameters) == 0:
                module.main()
            elif len(sig.parameters) == 1:
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

