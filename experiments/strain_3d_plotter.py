"""Visualise strain worksheet metrics across 3D scatter combinations."""

from __future__ import annotations

import itertools
import logging
import re
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib import cm
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.backends import wants_matplotlib, wants_origin
from plotting.utils import (
    ensure_app_theme,
    install_standard_menu,
    origin_session,
    schedule_origin_release,
)

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


def _extract_element_counts(composition: str) -> dict[str, float]:
    """Extract Ni/Fe/Ga/Co counts from a composition string."""

    counts = {"Ni": 0.0, "Fe": 0.0, "Ga": 0.0, "Co": 0.0}
    if not composition:
        return counts

    for element, value in re.findall(r"(Ni|Fe|Ga|Co)\s*(\d+(?:\.\d+)?)", composition):
        try:
            counts[element] = float(value)
        except ValueError:
            continue
    return counts


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
        self.resize(1400, 900)

        self.logger = logging.getLogger("strain_3d_plotter")
        self.logger.setLevel(logging.INFO)
        self.settings = QtCore.QSettings("MicrowireLab", "Strain3DPlotter")
        self._floating_windows: list[QtWidgets.QMainWindow] = []

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

        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        form.addRow("Output backend", self.backend_combo)

        layout.addLayout(form)

        buttons_row = QtWidgets.QHBoxLayout()
        self.run_button = QtWidgets.QPushButton("Generate plots")
        self.run_button.clicked.connect(self._generate_plots)
        buttons_row.addWidget(self.run_button)

        self.fullscreen_button = QtWidgets.QPushButton("Open selected plot in new window")
        self.fullscreen_button.setEnabled(False)
        self.fullscreen_button.clicked.connect(self._open_fullscreen_plot)
        buttons_row.addWidget(self.fullscreen_button)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(self.splitter, 1)

        self.tab_widget = QtWidgets.QTabWidget()
        self.splitter.addWidget(self.tab_widget)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Load a worksheet to generate 3D scatter plots…")
        self.splitter.addWidget(self.log_view)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setChildrenCollapsible(False)

        self.tab_widget.currentChanged.connect(self._update_fullscreen_state)

        install_standard_menu(self, help_topic="strain_3d_plotter", console=self.log_view)

    def _load_settings(self) -> None:
        value = self.settings.value("input_path", "")
        if isinstance(value, str):
            self.input_edit.setText(value)
        backend = self.settings.value("backend", "Matplotlib")
        if isinstance(backend, str):
            index = self.backend_combo.findText(backend, QtCore.Qt.MatchFlag.MatchFixedString)
            if index >= 0:
                self.backend_combo.setCurrentIndex(index)

    def _save_settings(self) -> None:
        self.settings.setValue("input_path", self.input_edit.text())
        self.settings.setValue("backend", self.backend_combo.currentText())
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

    def _update_fullscreen_state(self) -> None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            self.fullscreen_button.setEnabled(False)
            return
        has_data = tab.property("plot_data") is not None and tab.property("plot_combo") is not None
        self.fullscreen_button.setEnabled(has_data)

    def _open_fullscreen_plot(self) -> None:
        tab = self.tab_widget.currentWidget()
        if tab is None:
            return

        subset = tab.property("plot_data")
        combo = tab.property("plot_combo")
        title = tab.property("plot_title") or self.tab_widget.tabText(self.tab_widget.currentIndex())
        if subset is None or combo is None:
            QtWidgets.QMessageBox.information(
                self,
                "Strain 3D Plot Explorer",
                "Select a tab generated from worksheet data before opening a full-screen plot.",
            )
            return

        window = PlotWindow(self, title, subset, combo)
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.showMaximized()
        self._floating_windows.append(window)
        window.destroyed.connect(lambda: self._floating_windows.remove(window) if window in self._floating_windows else None)

    def _generate_plots(self) -> None:
        self.tab_widget.clear()
        self.log_view.clear()
        self.fullscreen_button.setEnabled(False)

        for window in list(self._floating_windows):
            try:
                window.close()
            except Exception:
                pass
        self._floating_windows.clear()

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
            lowered_label = label.lower()
            if "m length" in lowered_label or "a length" in lowered_label:
                continue
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
            for element, value in _extract_element_counts(composition_label).items():
                record[f"{element} (%)"] = value
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
            if label not in {"Microwire", "Composition"}
            and plot_df[label].notna().sum() >= 2
            and plot_df[label].dropna().nunique() >= 2
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

        backend_choice = self.backend_combo.currentText()
        self._save_settings()

        render_matplotlib = wants_matplotlib(backend_choice)
        export_origin = wants_origin(backend_choice)

        self._append_log(f"Loaded {len(records)} rows with strain measurements.")
        self._append_log(f"Prepared {len(combinations)} 3D combinations.")

        if render_matplotlib:
            self._render_matplotlib_tabs(plot_df, combinations)
        else:
            self.tab_widget.setVisible(False)

        if export_origin:
            self._export_origin(plot_df, combinations)

        if not render_matplotlib and not export_origin:
            self._append_log("No backends selected—nothing to generate.")

    def _render_matplotlib_tabs(self, plot_df: pd.DataFrame, combinations: List[Tuple[str, str, str]]) -> None:
        self.tab_widget.setVisible(True)
        generated = 0

        for combo in combinations:
            subset = plot_df[["Microwire", *combo]].dropna()
            if subset.empty:
                continue
            title = " vs ".join(combo)
            fig = Figure(figsize=(9.5, 7.0))
            ax = fig.add_subplot(111, projection="3d")
            self._draw_scatter(ax, subset, combo)
            fig.tight_layout()

            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.addWidget(canvas)
            tab.setProperty("plot_combo", combo)
            tab.setProperty("plot_data", subset.copy())
            tab.setProperty("plot_title", title)
            self.tab_widget.addTab(tab, title)
            generated += 1

        if generated == 0:
            QtWidgets.QMessageBox.information(
                self,
                "Strain 3D Plot Explorer",
                "No complete data combinations were available for plotting.",
            )
        else:
            self._append_log(
                "Finished generating Matplotlib plots. Use the tabs above to review them."
            )
        self._update_fullscreen_state()

    @staticmethod
    def _draw_scatter(ax, subset: pd.DataFrame, combo: Tuple[str, str, str]) -> None:
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
            ax.text(x, y, z, label_text, fontsize=9)

        ax.set_xlabel(combo[0])
        ax.set_ylabel(combo[1])
        ax.set_zlabel(combo[2])
        ax.set_title(" vs ".join(combo))

    def _export_origin(self, plot_df: pd.DataFrame, combinations: List[Tuple[str, str, str]]) -> None:
        try:
            with origin_session() as op:
                schedule_origin_release()
                exported = 0
                for combo in combinations:
                    subset = plot_df[["Microwire", *combo]].dropna()
                    if subset.empty:
                        continue
                    try:
                        self._build_origin_graph(op, subset, combo)
                        exported += 1
                    except Exception as exc:  # pragma: no cover - Origin specific
                        self._append_log(f"Origin plot failed for {' vs '.join(combo)}: {exc}")
                if exported:
                    self._append_log(
                        f"Sent {exported} scatter combinations to Origin."
                    )
                else:
                    self._append_log(
                        "No Origin plots were generated because every combination was empty."
                    )
        except (ModuleNotFoundError, ImportError):
            self._append_log(
                "OriginPro is not installed. Install the originpro package to enable Origin output."
            )
        except Exception as exc:  # pragma: no cover - Origin specific
            self._append_log(f"Unexpected Origin error: {exc}")

    def _build_origin_graph(
        self,
        origin_any,
        subset: pd.DataFrame,
        combo: Tuple[str, str, str],
    ) -> None:
        title = " vs ".join(combo)
        safe_title = self._escape_origin_text(title)

        book = origin_any.new_book('w', lname=self._origin_book_name(combo))
        book.activate()
        sheet = book[0]

        data = subset[[combo[0], combo[1], combo[2]]].astype(float).copy()
        data.columns = ["X", "Y", "Z"]
        sheet.from_df(data)
        try:
            sheet.cols_axis('XYZ')
        except Exception:
            pass

        try:
            sheet.add_cols(1)
            sheet.from_list(3, subset["Microwire"].tolist())
            sheet.set_label(3, "Microwire")
        except Exception:
            pass

        book.activate()
        origin_any.lt_exec('worksheet -s 0 0 -1 2; worksheet -t plot3d scatter;')
        origin_any.lt_exec('page.antialias=1; layer -aa 1;')
        origin_any.lt_exec(f'title -s "{safe_title}";')

    def _origin_book_name(self, combo: Tuple[str, str, str]) -> str:
        label = "_".join(combo)
        sanitized = "".join(ch if ch.isalnum() else "_" for ch in label)
        return f"Strain3D_{sanitized[:30]}"

    def _escape_origin_text(self, text: str) -> str:
        return text.replace("\"", "''")


class PlotWindow(QtWidgets.QMainWindow):
    """Floating window for reviewing a Matplotlib scatter plot full screen."""

    def __init__(self, parent: QtWidgets.QWidget | None, title: str, subset: pd.DataFrame, combo: Tuple[str, str, str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{title} — Strain 3D Plot Explorer")
        self._subset = subset.copy()
        self._combo = combo

        canvas = FigureCanvas(Figure(figsize=(11.0, 8.5)))
        self.setCentralWidget(canvas)

        fig = canvas.figure
        fig.clear()
        ax = fig.add_subplot(111, projection="3d")
        Strain3DPlotter._draw_scatter(ax, self._subset, self._combo)
        fig.tight_layout()


def main() -> QtWidgets.QWidget | None:  # pragma: no cover - thin launcher wrapper
    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme(app)
    widget = Strain3DPlotter()
    widget.show()
    if created_app:
        app.exec()
        return None
    return widget


if __name__ == "__main__":  # pragma: no cover - manual launch
    main()
