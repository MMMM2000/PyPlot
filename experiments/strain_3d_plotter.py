"""Visualise strain worksheet metrics across 3D scatter combinations."""

from __future__ import annotations

import itertools
import logging
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib import cm
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.utils import ensure_app_theme, install_standard_menu

from experiments.microwire_data_builder.core import _parse_numeric, _parse_strain_float


def _clean_header(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _pretty_header(value: object, index: int) -> str:
    cleaned = _clean_header(value)
    if cleaned and not cleaned.lower().startswith("unnamed"):
        return cleaned
    return f"Column {index + 1}"


def _build_column_map(columns: Sequence[object]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(columns):
        name = _clean_header(header)
        lowered = name.lower()
        if not lowered:
            continue
        if "composition" in lowered:
            mapping.setdefault("composition", idx)
        elif "microwire" in lowered or "wire" in lowered:
            mapping.setdefault("microwire", idx)
        elif "strain" in lowered or "%" in lowered:
            mapping.setdefault("strain", idx)
        elif "status" in lowered or "broke" in lowered:
            mapping.setdefault("status", idx)
    return mapping


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


class Strain3DPlotter(QtWidgets.QWidget):
    """Widget that renders 3D scatter plots for strain worksheet metrics."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Strain 3D Plot Explorer")
        self.resize(920, 640)

        self.logger = logging.getLogger("strain_3d_plotter")
        self.logger.setLevel(logging.INFO)
        self.settings = QtCore.QSettings("MicrowireLab", "Strain3DPlotter")

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------ ui helpers
    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self.input_edit = QtWidgets.QLineEdit()
        self.input_button = QtWidgets.QPushButton("Browse…")
        self.input_button.clicked.connect(self._choose_input_file)
        input_row = QtWidgets.QHBoxLayout()
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self.input_button)
        form.addRow("Strain worksheet", input_row)

        layout.addLayout(form)

        self.run_button = QtWidgets.QPushButton("Generate plots")
        self.run_button.clicked.connect(self._generate_plots)
        layout.addWidget(self.run_button)

        self.tab_widget = QtWidgets.QTabWidget()
        layout.addWidget(self.tab_widget, 1)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Load a worksheet to generate 3D scatter plots…")
        layout.addWidget(self.log_view, 1)

        install_standard_menu(self, help_topic="strain_3d_plotter", console=self.log_view)

    def _load_settings(self) -> None:
        value = self.settings.value("input_path", "")
        if isinstance(value, str):
            self.input_edit.setText(value)

    def _save_settings(self) -> None:
        self.settings.setValue("input_path", self.input_edit.text())
        self.settings.sync()

    # ------------------------------------------------------------------ file selection
    def _choose_input_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select strain worksheet",
            self.input_edit.text() or str(Path.home()),
            "Excel files (*.xlsx *.xlsm *.xls)",
        )
        if filename:
            self.input_edit.setText(filename)
            self._save_settings()

    # ------------------------------------------------------------------ plotting logic
    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.logger.info(message)

    def _generate_plots(self) -> None:
        self.tab_widget.clear()
        self.log_view.clear()

        path = Path(self.input_edit.text().strip())
        if not path.exists():
            QtWidgets.QMessageBox.warning(self, "Strain 3D Plot Explorer", "Please select an existing worksheet file.")
            return

        try:
            df = pd.read_excel(path)
        except Exception as exc:  # pragma: no cover - user feedback
            QtWidgets.QMessageBox.critical(
                self,
                "Strain 3D Plot Explorer",
                f"Failed to read worksheet:\n{exc}",
            )
            return

        df = df.dropna(how="all")
        if df.empty:
            QtWidgets.QMessageBox.information(self, "Strain 3D Plot Explorer", "The worksheet does not contain any data.")
            return

        columns = list(df.columns)
        mapping = _build_column_map(columns)
        composition_idx = mapping.get("composition") if mapping.get("composition") is not None else (0 if columns else None)
        microwire_idx = mapping.get("microwire") if mapping.get("microwire") is not None else (1 if len(columns) > 1 else None)
        strain_idx = mapping.get("strain")
        status_idx = mapping.get("status")

        if strain_idx is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Strain 3D Plot Explorer",
                "Could not locate a strain column. Ensure the worksheet header contains 'strain'.",
            )
            return

        strain_label = _pretty_header(columns[strain_idx], strain_idx)

        numeric_columns: List[Tuple[int, str]] = []
        for idx, header in enumerate(columns):
            if idx == strain_idx or idx == composition_idx or idx == microwire_idx or idx == status_idx:
                continue
            label = _pretty_header(header, idx)
            numeric_columns.append((idx, label))

        records = []
        for row_index, row in df.iterrows():
            # Skip repeated header rows that sometimes appear in worksheets
            if composition_idx is not None:
                if _clean_cell(row.iloc[composition_idx]).lower() == "composition":
                    continue
            strain_value = _parse_strain_float(row.iloc[strain_idx])
            if strain_value is None:
                continue
            microwire_label = _clean_cell(row.iloc[microwire_idx]) if microwire_idx is not None else ""
            composition_label = _clean_cell(row.iloc[composition_idx]) if composition_idx is not None else ""
            record = {
                "Microwire": microwire_label or composition_label or f"Row {row_index}",
                "Composition": composition_label,
                strain_label: strain_value,
            }
            for col_idx, label in numeric_columns:
                parsed = _parse_numeric(row.iloc[col_idx])
                record[label] = parsed
            records.append(record)

        if not records:
            QtWidgets.QMessageBox.information(
                self,
                "Strain 3D Plot Explorer",
                "No rows with strain measurements were found in the worksheet.",
            )
            return

        plot_df = pd.DataFrame(records)
        valid_numeric_labels = [
            label
            for label in plot_df.columns
            if label not in {"Microwire", "Composition"} and plot_df[label].notna().sum() >= 2
        ]
        if strain_label not in valid_numeric_labels:
            valid_numeric_labels.insert(0, strain_label)

        combinations = list(itertools.combinations(valid_numeric_labels, 3))
        if not combinations:
            QtWidgets.QMessageBox.information(
                self,
                "Strain 3D Plot Explorer",
                "Not enough numeric columns with data to build 3D plots.",
            )
            return

        self._append_log(f"Loaded {len(records)} rows with strain measurements.")
        self._append_log(f"Generating {len(combinations)} 3D scatter plots…")

        for combo in combinations:
            subset = plot_df[["Microwire", *combo]].dropna()
            if subset.empty:
                continue
            title = " vs ".join(combo)
            fig = Figure(figsize=(6.5, 5.0))
            ax = fig.add_subplot(111, projection="3d")

            xs = subset[combo[0]].to_numpy(dtype=float)
            ys = subset[combo[1]].to_numpy(dtype=float)
            zs = subset[combo[2]].to_numpy(dtype=float)
            labels = subset["Microwire"].tolist()

            if xs.max() != xs.min():
                norm = (xs - xs.min()) / (xs.max() - xs.min())
            else:
                norm = [0.5] * len(xs)
            colors = cm.viridis(norm)
            ax.scatter(xs, ys, zs, c=colors, s=60, depthshade=True)

            for x, y, z, label_text in zip(xs, ys, zs, labels):
                ax.text(x, y, z, label_text, fontsize=8)

            ax.set_xlabel(combo[0])
            ax.set_ylabel(combo[1])
            ax.set_zlabel(combo[2])
            ax.set_title(title)

            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            self.tab_widget.addTab(tab, title)

        if self.tab_widget.count() == 0:
            QtWidgets.QMessageBox.information(
                self,
                "Strain 3D Plot Explorer",
                "No complete data combinations were available for plotting.",
            )
            return

        self._append_log("Finished generating plots. Use the tabs above to review them.")


def main() -> QtWidgets.QWidget | None:  # pragma: no cover - thin launcher wrapper
    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme()
    widget = Strain3DPlotter()
    widget.show()
    if created_app:
        app.exec()
        return None
    return widget


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()
