"""Tool for converting legacy current annealing logs from amperes to milliamperes."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable, Tuple

from PyQt6 import QtCore, QtWidgets

from plotting.utils import ensure_app_theme, install_standard_menu


COLUMN_HEADER = "Current (mA)\tVoltage (V)\tResistance (Ohm)"
MILLIAMP_HEADER_RE = re.compile(r"(?:current|i)\s*[^\r\n]{0,12}[\[(]\s*m\s*a", re.IGNORECASE)
AMP_HEADER_RE = re.compile(r"(?:current|i)\s*[^\r\n]{0,12}[\[(]\s*a", re.IGNORECASE)


def _format_value(value: float) -> str:
    text = format(float(value), ".12g")
    return "0" if text == "-0" else text


def _detect_unit_hint(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    candidate = stripped.lstrip("#").strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    if MILLIAMP_HEADER_RE.search(lowered) or "milli" in lowered:
        return "ma"
    if AMP_HEADER_RE.search(lowered):
        if "ma" not in lowered:
            return "a"
    if "amp" in lowered and ("current" in lowered or lowered.startswith("i")):
        if "ma" not in lowered:
            return "a"
    return None


def _is_header_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    candidate = stripped.lstrip("#").strip()
    if not candidate:
        return False
    lowered = candidate.lower()
    keywords = ("current", "voltage", "resistance", "i(", "u(", "r(")
    hits = sum(1 for key in keywords if key in lowered)
    if hits >= 2:
        return True
    normalized = re.sub(r"\s+", " ", candidate).lower()
    target = COLUMN_HEADER.replace("\t", " ").lower()
    return normalized == target


def convert_file(path: Path) -> Tuple[str, str]:
    """Convert ``path`` in-place. Returns (status, detail)."""

    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return "error", f"failed to read: {exc}"

    unit_hint: str | None = None
    for probe in raw_lines[:10]:
        hint = _detect_unit_hint(probe)
        if hint:
            unit_hint = hint
            break

    needs_conversion = unit_hint != "ma"

    output_lines: list[str] = []
    converted = False
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_header_line(line):
            continue
        if stripped.startswith("#"):
            continue
        parts = stripped.replace(",", ".").split()
        if not parts:
            continue
        try:
            current_value = float(parts[0])
        except ValueError:
            continue
        if not math.isfinite(current_value):
            continue
        if needs_conversion:
            current_value *= 1000.0
            converted = True
        parts[0] = _format_value(current_value)
        output_lines.append("\t".join(parts))

    if not output_lines:
        return "skipped", "no numeric data"

    final_lines = [COLUMN_HEADER]
    final_lines.extend(output_lines)
    try:
        path.write_text("\n".join(final_lines) + "\n", encoding="utf-8")
    except OSError as exc:
        return "error", f"failed to write: {exc}"

    if converted:
        return "converted", "converted"
    return "normalized", "header normalised"


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
            "rewrite the first column so currents are stored in milliamperes and ensure the "
            "standard header is present so you can spot already converted files."
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
        converted = normalized = skipped = errors = 0
        for path in files:
            status, detail = convert_file(path)
            if status == "converted":
                converted += 1
                self._log(f"Converted {path.name}")
            elif status == "normalized":
                normalized += 1
                self._log(f"Normalised {path.name}")
            elif status == "skipped":
                skipped += 1
                self._log(f"Skipped {path.name} ({detail})")
            else:
                errors += 1
                self._log(f"Error {path.name}: {detail}")
        summary = (
            "Finished. Converted {converted} file(s), normalised {normalized}, skipped {skipped}, "
            "encountered {errors} error(s)."
        ).format(converted=converted, normalized=normalized, skipped=skipped, errors=errors)
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

