"""GUI for merging database strain data into the standalone strain worksheet."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple
import sys

import pandas as pd
from PyQt6 import QtCore, QtWidgets

from plotting.utils import ensure_app_theme, install_standard_menu

from microwire_data_builder.core import (
    STRAIN_COLUMN,
    StrainRecord,
    _load_strain_records,
    _microwire_tuple_from_label,
    _parse_numeric,
)


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not pd.isna(value):
        return str(value).strip()
    if pd.isna(value):
        return ""
    return str(value).strip()


def _build_column_map(columns: Iterable[object]) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    for idx, header in enumerate(columns):
        key = _clean_cell(header).lower()
        if not key:
            continue
        if "composition" in key:
            mapping.setdefault("composition", idx)
        elif "microwire" in key or "wire" in key:
            mapping.setdefault("microwire", idx)
        elif key.startswith("m") and "length" in key:
            mapping.setdefault("m_length", idx)
        elif key.startswith("a") and "length" in key:
            mapping.setdefault("a_length", idx)
        elif "strain" in key or "%" in key:
            mapping.setdefault("strain", idx)
        elif "status" in key or "note" in key or "broke" in key:
            mapping.setdefault("status", idx)
    return mapping


def _parse_database_strain(value: object) -> Tuple[Optional[float], bool]:
    text = _clean_cell(value)
    if not text:
        return None, False
    lowered = text.lower()
    if "broke" in lowered:
        return None, True
    parsed = _parse_numeric(text)
    if parsed is None:
        return None, False
    return float(parsed), False


class StrainWorksheetUpdater(QtWidgets.QWidget):
    """Widget that merges database strain data into the strain worksheet."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Strain Worksheet Updater")
        self.resize(640, 360)

        self.logger = logging.getLogger("strain_updater")
        self.logger.setLevel(logging.INFO)
        self.settings = QtCore.QSettings("MicrowireLab", "StrainWorksheetUpdater")

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.strain_edit = QtWidgets.QLineEdit()
        self.strain_button = QtWidgets.QPushButton("Browse…")
        self.strain_button.clicked.connect(self._choose_strain_file)
        strain_row = QtWidgets.QHBoxLayout()
        strain_row.addWidget(self.strain_edit, 1)
        strain_row.addWidget(self.strain_button)
        form.addRow("Strain worksheet", strain_row)

        self.database_edit = QtWidgets.QLineEdit()
        self.database_button = QtWidgets.QPushButton("Browse…")
        self.database_button.clicked.connect(self._choose_database_file)
        database_row = QtWidgets.QHBoxLayout()
        database_row.addWidget(self.database_edit, 1)
        database_row.addWidget(self.database_button)
        form.addRow("Database file", database_row)

        self.output_edit = QtWidgets.QLineEdit()
        self.output_button = QtWidgets.QPushButton("Browse…")
        self.output_button.clicked.connect(self._choose_output_file)
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_button)
        form.addRow("Save as", output_row)

        layout.addLayout(form)

        self.run_button = QtWidgets.QPushButton("Update worksheet")
        self.run_button.clicked.connect(self._run_update)
        layout.addWidget(self.run_button)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Status messages will appear here…")
        layout.addWidget(self.log_view, 1)

        install_standard_menu(
            self,
            help_topic="strain_updater",
            console=self.log_view,
            open_file=self._choose_strain_file,
            open_folder=self._choose_output_folder,
        )

    # ------------------------------------------------------------------ settings helpers
    def _load_settings(self) -> None:
        strain_value = self.settings.value("strain_path", "")
        if isinstance(strain_value, str):
            self.strain_edit.setText(strain_value)
        db_value = self.settings.value("database_path", "")
        if isinstance(db_value, str):
            self.database_edit.setText(db_value)
        out_value = self.settings.value("output_path", "")
        if isinstance(out_value, str):
            self.output_edit.setText(out_value)

    def _save_settings(self) -> None:
        self.settings.setValue("strain_path", self.strain_edit.text())
        self.settings.setValue("database_path", self.database_edit.text())
        self.settings.setValue("output_path", self.output_edit.text())
        self.settings.sync()

    # ------------------------------------------------------------------ file selection slots
    def _choose_strain_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select strain worksheet",
            self.strain_edit.text() or str(Path.home()),
            "Excel files (*.xlsx *.xlsm *.xls)",
        )
        if filename:
            self.strain_edit.setText(filename)
            if not self.output_edit.text().strip():
                suggestion = Path(filename)
                self.output_edit.setText(str(suggestion.with_name(f"{suggestion.stem}_updated.xlsx")))
            self._save_settings()

    def _choose_database_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select database worksheet",
            self.database_edit.text() or str(Path.home()),
            "Data files (*.xlsx *.xlsm *.xls *.csv)",
        )
        if filename:
            self.database_edit.setText(filename)
            self._save_settings()

    def _choose_output_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save updated strain worksheet",
            self.output_edit.text() or self.strain_edit.text() or str(Path.home() / "strain_updated.xlsx"),
            "Excel files (*.xlsx)",
        )
        if filename:
            if not filename.lower().endswith(".xlsx"):
                filename += ".xlsx"
            self.output_edit.setText(filename)
            self._save_settings()

    def _choose_output_folder(self) -> None:
        """Select an output directory via the shared File menu."""

        current = self.output_edit.text().strip()
        if current:
            try:
                start_dir = str(Path(current).expanduser().resolve().parent)
            except Exception:
                start_dir = str(Path.home())
        elif self.strain_edit.text().strip():
            try:
                start_dir = str(Path(self.strain_edit.text().strip()).expanduser().resolve().parent)
            except Exception:
                start_dir = str(Path.home())
        else:
            start_dir = str(Path.home())

        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select output folder",
            start_dir,
        )
        if not directory:
            return

        suggestion = Path(directory) / (Path(current).name or "strain_updated.xlsx")
        if not suggestion.suffix:
            suggestion = suggestion.with_suffix(".xlsx")
        self.output_edit.setText(str(suggestion))
        self._save_settings()

    # ------------------------------------------------------------------ merge logic
    def _run_update(self) -> None:
        strain_path = Path(self.strain_edit.text().strip())
        database_path = Path(self.database_edit.text().strip())
        output_path = Path(self.output_edit.text().strip())

        if not strain_path.exists():
            QtWidgets.QMessageBox.warning(self, "Strain Worksheet Updater", "Please select an existing strain worksheet.")
            return
        if not database_path.exists():
            QtWidgets.QMessageBox.warning(self, "Strain Worksheet Updater", "Please select an existing database file.")
            return
        if not output_path.parent.exists():
            QtWidgets.QMessageBox.warning(self, "Strain Worksheet Updater", "The output folder does not exist.")
            return

        try:
            updated = self._merge_files(strain_path, database_path)
            updated.to_excel(output_path, index=False)
        except Exception as exc:
            self.logger.exception("Failed to update strain worksheet")
            QtWidgets.QMessageBox.critical(
                self,
                "Strain Worksheet Updater",
                f"Updating the worksheet failed: {exc}",
            )
            return

        self._append_log(f"Updated strain worksheet written to {output_path}")
        QtWidgets.QMessageBox.information(
            self,
            "Strain Worksheet Updater",
            "The strain worksheet has been updated.",
        )

    def _merge_files(self, strain_path: Path, database_path: Path) -> pd.DataFrame:
        existing_records = _load_strain_records([strain_path], self.logger)
        strain_df = pd.read_excel(strain_path)
        column_map = _build_column_map(strain_df.columns)

        db_df = self._load_database_frame(database_path)
        db_column_map = _build_column_map(db_df.columns)

        db_updates: Dict[Tuple[str, int, int], Tuple[Optional[float], bool]] = {}
        comp_idx = db_column_map.get("composition")
        micro_idx = db_column_map.get("microwire")
        strain_idx = db_column_map.get("strain")
        status_idx = db_column_map.get("status")
        if comp_idx is not None and micro_idx is not None:
            for values in db_df.itertuples(index=False, name=None):
                composition = _clean_cell(values[comp_idx])
                microwire_label = _clean_cell(values[micro_idx])
                key = _microwire_tuple_from_label(microwire_label)
                if not composition or not key:
                    continue
                percent, broke = (None, False)
                if strain_idx is not None and strain_idx < len(values):
                    percent, broke = _parse_database_strain(values[strain_idx])
                if not broke and status_idx is not None and status_idx < len(values):
                    if _clean_cell(values[status_idx]).lower() == "broke":
                        broke = True
                        percent = None
                if percent is None and not broke:
                    continue
                db_updates[(composition, key[0], key[1])] = (percent, broke)

        merged: Dict[Tuple[str, int, int], StrainRecord] = {}
        for key, record in existing_records.items():
            label = record.microwire_label or f"{record.draw}/{record.piece}"
            merged[key] = StrainRecord(
                composition=record.composition,
                draw=record.draw,
                piece=record.piece,
                microwire_label=label,
                m_length=record.m_length,
                a_length=record.a_length,
                percent=record.percent,
                broke=record.broke,
                source=record.source,
            )

        for key, (percent, broke) in db_updates.items():
            record = merged.get(key)
            label = f"{key[1]}/{key[2]}"
            if record:
                merged[key] = StrainRecord(
                    composition=record.composition,
                    draw=record.draw,
                    piece=record.piece,
                    microwire_label=record.microwire_label or label,
                    m_length=record.m_length,
                    a_length=record.a_length,
                    percent=percent if percent is not None else record.percent,
                    broke=broke or record.broke,
                    source=record.source,
                )
            else:
                merged[key] = StrainRecord(
                    composition=key[0],
                    draw=key[1],
                    piece=key[2],
                    microwire_label=label,
                    m_length=None,
                    a_length=None,
                    percent=percent,
                    broke=broke,
                    source=database_path,
                )

        rows = []
        for key in sorted(merged, key=lambda item: (item[0], item[1], item[2])):
            record = merged[key]
            rows.append(
                {
                    "Composition": record.composition,
                    "Microwire": record.microwire_label or f"{record.draw}/{record.piece}",
                    "M length": record.m_length,
                    "A length": record.a_length,
                    "Strain %": record.percent if not record.broke else None,
                    "Status": "broke" if record.broke else None,
                }
            )

        # Preserve any rows that could not be parsed earlier
        comp_idx = column_map.get("composition")
        micro_idx = column_map.get("microwire")
        if comp_idx is not None and micro_idx is not None:
            for values in strain_df.itertuples(index=False, name=None):
                composition = _clean_cell(values[comp_idx])
                microwire_label = _clean_cell(values[micro_idx])
                if not composition or not microwire_label:
                    continue
                if _microwire_tuple_from_label(microwire_label):
                    continue
                m_length = column_map.get("m_length")
                a_length = column_map.get("a_length")
                strain_idx = column_map.get("strain")
                status_idx = column_map.get("status")
                rows.append(
                    {
                        "Composition": composition,
                        "Microwire": microwire_label,
                        "M length": values[m_length] if m_length is not None and m_length < len(values) else None,
                        "A length": values[a_length] if a_length is not None and a_length < len(values) else None,
                        "Strain %": values[strain_idx]
                        if strain_idx is not None and strain_idx < len(values)
                        else None,
                        "Status": _clean_cell(values[status_idx])
                        if status_idx is not None and status_idx < len(values)
                        else None,
                    }
                )

        result = pd.DataFrame(
            rows,
            columns=["Composition", "Microwire", "M length", "A length", "Strain %", "Status"],
        )
        return result

    def _load_database_frame(self, path: Path) -> pd.DataFrame:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return pd.read_csv(path)
        return pd.read_excel(path)

    def _append_log(self, message: str) -> None:
        self.logger.info(message)
        self.log_view.appendPlainText(message)


def _ensure_app() -> Tuple[QtWidgets.QApplication, bool]:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True
    ensure_app_theme(app)
    return app, owns_app


def main() -> QtWidgets.QWidget | None:
    app, owns_app = _ensure_app()
    window = StrainWorksheetUpdater()
    window.show()
    if owns_app:
        app.exec()
    return window


def run_app() -> None:
    app, owns_app = _ensure_app()
    window = StrainWorksheetUpdater()
    window.show()
    if owns_app:
        sys.exit(app.exec())
    # If another component owns the QApplication the launcher will keep running.


__all__ = ["main", "run_app", "StrainWorksheetUpdater"]
