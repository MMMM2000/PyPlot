"""Tool for converting legacy current annealing logs from amperes to milliamperes."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Tuple

from PyQt6 import QtCore, QtWidgets

from plotting.utils import ensure_app_theme, install_standard_menu


CONVERSION_MARKER = "# Current annealing current units: mA"


def _format_value(value: float) -> str:
    text = format(float(value), ".12g")
    return "0" if text == "-0" else text


def _update_comment(line: str) -> str:
    replacements = (
        ("I(A)", "I(mA)"),
        ("Current (A)", "Current (mA)"),
        ("Current[A]", "Current[mA]"),
        ("I [A]", "I [mA]"),
    )
    updated = line
    for old, new in replacements:
        if old in updated:
            updated = updated.replace(old, new)
    return updated


def convert_file(path: Path) -> Tuple[str, str]:
    """Convert ``path`` in-place. Returns (status, detail)."""

    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return "error", f"failed to read: {exc}"

    if any(CONVERSION_MARKER in line for line in raw_lines[:5]):
        return "skipped", "already converted"

    converted_lines: list[str] = []
    converted = False
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            converted_lines.append(line)
            continue
        if stripped.startswith("#"):
            converted_lines.append(_update_comment(line))
            continue
        parts = stripped.replace(",", ".").split()
        if not parts:
            converted_lines.append(line)
            continue
        try:
            current_a = float(parts[0])
        except ValueError:
            converted_lines.append(line)
            continue
        if not math.isfinite(current_a):
            converted_lines.append(line)
            continue
        converted = True
        parts[0] = _format_value(current_a * 1000.0)
        converted_lines.append("\t".join(parts))

    if not converted:
        return "skipped", "no numeric data"

    if not any(line.startswith("#") for line in converted_lines):
        converted_lines.insert(0, "# Current (mA)\tVoltage (V)\tResistance (Ohm)")

    converted_lines.insert(0, CONVERSION_MARKER)
    try:
        path.write_text("\n".join(converted_lines) + "\n", encoding="utf-8")
    except OSError as exc:
        return "error", f"failed to write: {exc}"

    return "converted", "converted"


class CurrentAnnealingConverter(QtWidgets.QWidget):
    """Widget that converts legacy current annealing logs to milliamps."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Current Annealing Unit Converter")
        self.resize(520, 320)

        self.settings = QtCore.QSettings("MicrowireLab", "CurrentAnnealingUnitConverter")

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------ ui helpers
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        intro = QtWidgets.QLabel(
            "Select a folder containing legacy current annealing logs. The converter will "
            "rewrite the first column so currents are stored in milliamperes. A marker "
            "is added to each converted file to avoid double conversion."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.folder_edit = QtWidgets.QLineEdit()
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.clicked.connect(self._choose_folder)
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse_btn)
        form.addRow("Data folder", folder_row)

        self.recursive_cb = QtWidgets.QCheckBox("Process subfolders recursively")
        self.recursive_cb.setChecked(True)
        form.addRow("", self.recursive_cb)

        layout.addLayout(form)

        self.run_button = QtWidgets.QPushButton("Convert to mA")
        self.run_button.clicked.connect(self._run_conversion)
        layout.addWidget(self.run_button)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Conversion progress will appear here…")
        layout.addWidget(self.log_view, 1)

        install_standard_menu(
            self,
            help_topic="experiment_current_annealing_converter",
            console=self.log_view,
            open_folder=self._choose_folder,
        )

    # ------------------------------------------------------------------ settings
    def _load_settings(self) -> None:
        folder = self.settings.value("folder", "", type=str)
        if folder:
            self.folder_edit.setText(folder)
        recursive = self.settings.value("recursive", 1, type=int)
        self.recursive_cb.setChecked(bool(recursive))

    def _save_settings(self) -> None:
        self.settings.setValue("folder", self.folder_edit.text())
        self.settings.setValue("recursive", int(self.recursive_cb.isChecked()))
        self.settings.sync()

    # ------------------------------------------------------------------ actions
    def _choose_folder(self) -> None:
        start = self.folder_edit.text().strip() or str(Path.home())
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select current annealing folder",
            start,
        )
        if directory:
            self.folder_edit.setText(directory)
            self._save_settings()

    def _iter_files(self, base: Path) -> Iterable[Path]:
        if self.recursive_cb.isChecked():
            yield from sorted(base.rglob("*.txt"))
        else:
            yield from sorted(base.glob("*.txt"))

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _run_conversion(self) -> None:
        folder_text = self.folder_edit.text().strip()
        if not folder_text:
            QtWidgets.QMessageBox.warning(self, "No folder", "Select a folder containing log files.")
            return
        folder = Path(folder_text)
        if not folder.is_dir():
            QtWidgets.QMessageBox.warning(self, "Invalid folder", "The selected folder does not exist.")
            return
        self._save_settings()
        self.log_view.clear()
        files = list(self._iter_files(folder))
        if not files:
            self._log("No .txt files were found in the selected folder.")
            return
        converted = skipped = errors = 0
        for path in files:
            status, detail = convert_file(path)
            if status == "converted":
                converted += 1
                self._log(f"Converted {path.name}")
            elif status == "skipped":
                skipped += 1
                self._log(f"Skipped {path.name} ({detail})")
            else:
                errors += 1
                self._log(f"Error {path.name}: {detail}")
        summary = (
            f"Finished. Converted {converted} file(s), skipped {skipped}, encountered {errors} error(s)."
        )
        self._log(summary)
        QtWidgets.QMessageBox.information(self, "Conversion complete", summary)


def main() -> QtWidgets.QWidget | None:
    app = QtWidgets.QApplication.instance()
    created = False
    if app is None:
        app = QtWidgets.QApplication([])
        created = True
    ensure_app_theme(app)
    widget = CurrentAnnealingConverter()
    widget.show()
    if created:
        app.exec()
        return None
    return widget


__all__ = ["main", "convert_file"]

