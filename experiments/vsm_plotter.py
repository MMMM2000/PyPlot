"""Visualise VSM hysteresis loops grouped by temperature and angle."""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd
from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from plotting.backends import wants_matplotlib, wants_origin
from plotting.utils import ensure_app_theme, install_standard_menu, origin_session, schedule_origin_release

HEADER_COLUMN_RE = re.compile(r"Column\\s+\\d+\\s*:\\s*(.+)")
WHITESPACE_RE = re.compile(r"[_\\s]+")
ANGLE_RE = re.compile(r"a(-?\\d+(?:\\.\\d+)?)", re.IGNORECASE)
TEMP_RE = re.compile(r"T(-?\\d+(?:\\.\\d+)?)", re.IGNORECASE)


@dataclass
class VSMMeasurement:
    path: Path
    temperature: float
    angle: float
    data: pd.DataFrame


def _normalise_column_name(raw: str, index: int) -> str:
    cleaned = raw.strip()
    if not cleaned:
        return f"Column {index}"
    primary = cleaned.split(",", 1)[0].strip()
    primary = re.sub(r"\\s+", " ", primary)
    return primary or f"Column {index}"


def _normalise_header_token(raw: str, index: int) -> str:
    """Best effort conversion of inline header tokens to friendly labels."""

    cleaned = raw.strip().strip("_")
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip()
    return cleaned or f"Column {index}"


def _read_vsm_file(path: Path) -> pd.DataFrame:
    columns: List[str] = []
    inline_header: List[str] | None = None
    sections: List[List[List[str]]] = []

    current_rows: List[List[str]] = []
    current_tokens: List[str] = []
    expected_columns: int | None = None
    in_data = False

    def _start_section() -> None:
        nonlocal current_rows, current_tokens, expected_columns, in_data
        current_rows = []
        current_tokens = []
        expected_columns = len(columns) or (len(inline_header) if inline_header else None)
        in_data = True

    def _finish_section() -> None:
        nonlocal current_rows, current_tokens, expected_columns, in_data
        if expected_columns and current_tokens:
            if len(current_tokens) == expected_columns:
                current_rows.append(current_tokens[:])
            current_tokens = []
        if current_rows:
            sections.append([row[:] for row in current_rows])
        current_rows = []
        current_tokens = []
        expected_columns = None
        in_data = False

    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if not in_data:
                match = HEADER_COLUMN_RE.match(stripped)
                if match:
                    columns.append(_normalise_column_name(match.group(1), len(columns)))
                    continue
                if stripped.startswith("@@"):
                    if stripped.startswith("@@Data") or stripped.startswith("@@Final Manipulated Data"):
                        _start_section()
                    continue
                if stripped and not stripped.startswith("@") and not columns:
                    parts = stripped.split()
                    if parts and not any(_looks_numeric(part) for part in parts):
                        if inline_header is None or len(parts) > len(inline_header):
                            inline_header = parts
                continue

            if stripped.startswith("@@END Data"):
                _finish_section()
                continue
            if stripped.startswith("@@"):
                continue
            if not stripped or stripped.startswith("New Section"):
                continue
            if stripped.startswith("@"):
                continue

            tokens = stripped.split()
            if not tokens:
                continue
            current_tokens.extend(tokens)
            if expected_columns is None:
                if columns:
                    expected_columns = len(columns)
                elif inline_header:
                    expected_columns = len(inline_header)
                else:
                    expected_columns = len(tokens)
            if expected_columns:
                while len(current_tokens) >= expected_columns:
                    row = current_tokens[:expected_columns]
                    current_rows.append(row)
                    current_tokens = current_tokens[expected_columns:]

    if in_data:
        _finish_section()

    for section in reversed(sections):
        if section:
            data_rows = section
            break
    else:
        raise ValueError("No data rows detected in VSM file")

    df = pd.DataFrame(data_rows, dtype=float)

    width = df.shape[1]
    resolved: List[str] = []
    source_names: List[str]
    if columns:
        source_names = columns
    elif inline_header:
        source_names = [_normalise_header_token(token, idx) for idx, token in enumerate(inline_header)]
    else:
        source_names = []

    for idx in range(width):
        if idx < len(source_names):
            name = source_names[idx]
        else:
            name = f"Column {idx}"
        if name in resolved:
            suffix = 2
            while f"{name} ({suffix})" in resolved:
                suffix += 1
            name = f"{name} ({suffix})"
        resolved.append(name)
    df.columns = resolved
    return df


def _looks_numeric(token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    try:
        float(token)
    except ValueError:
        return False
    return True


def _parse_temperature(path: Path) -> float | None:
    match = TEMP_RE.search(path.stem)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_angle(path: Path) -> float | None:
    match = ANGLE_RE.search(path.stem)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


class VSMPlotter(QtWidgets.QWidget):
    """Render hysteresis loops for VSM-HYS-DATA files."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VSM Plot Explorer")
        self.resize(1480, 940)

        self.logger = logging.getLogger("vsm_plotter")
        self.logger.setLevel(logging.INFO)
        self.settings = QtCore.QSettings("MicrowireLab", "VSMPlotter")

        self.measurements: List[VSMMeasurement] = []

        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        controls = QtWidgets.QFrame()
        controls.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        controls.setMinimumWidth(340)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setSpacing(10)

        mode_group = QtWidgets.QButtonGroup(self)
        self.file_radio = QtWidgets.QRadioButton("Select files")
        self.folder_radio = QtWidgets.QRadioButton("Select folder")
        mode_group.addButton(self.file_radio)
        mode_group.addButton(self.folder_radio)
        self.file_radio.setChecked(True)

        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.addWidget(self.file_radio)
        mode_layout.addWidget(self.folder_radio)
        controls_layout.addLayout(mode_layout)

        self.path_edit = QtWidgets.QLineEdit()
        browse_button = QtWidgets.QPushButton("Browse…")
        browse_button.clicked.connect(self._choose_input)
        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_button)
        controls_layout.addLayout(path_row)

        self.backend_combo = QtWidgets.QComboBox()
        self.backend_combo.addItems(["Matplotlib", "Origin", "Both"])
        controls_layout.addWidget(QtWidgets.QLabel("Output backend"))
        controls_layout.addWidget(self.backend_combo)

        self.temperature_combo = QtWidgets.QComboBox()
        self.temperature_combo.addItem("All temperatures", None)
        controls_layout.addWidget(QtWidgets.QLabel("Temperature filter"))
        controls_layout.addWidget(self.temperature_combo)

        self.x_axis_combo = QtWidgets.QComboBox()
        self.y_axis_combo = QtWidgets.QComboBox()
        controls_layout.addWidget(QtWidgets.QLabel("X axis"))
        controls_layout.addWidget(self.x_axis_combo)
        controls_layout.addWidget(QtWidgets.QLabel("Y axis"))
        controls_layout.addWidget(self.y_axis_combo)

        button_row = QtWidgets.QHBoxLayout()
        self.load_button = QtWidgets.QPushButton("Load data")
        self.load_button.clicked.connect(self._load_measurements)
        button_row.addWidget(self.load_button)
        self.plot_button = QtWidgets.QPushButton("Generate plots")
        self.plot_button.clicked.connect(self._generate_plots)
        self.plot_button.setEnabled(False)
        button_row.addWidget(self.plot_button)
        controls_layout.addLayout(button_row)

        controls_layout.addStretch(1)

        layout.addWidget(controls, 0)

        self.output_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        layout.addWidget(self.output_splitter, 1)

        self.tab_widget = QtWidgets.QTabWidget()
        self.output_splitter.addWidget(self.tab_widget)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Load VSM measurements to display hysteresis loops…")
        self.output_splitter.addWidget(self.log_view)
        self.output_splitter.setStretchFactor(0, 4)
        self.output_splitter.setStretchFactor(1, 1)
        self.output_splitter.setChildrenCollapsible(False)

        install_standard_menu(self, help_topic="vsm_plotter", console=self.log_view)

    def _load_settings(self) -> None:
        value = self.settings.value("last_path", "")
        if isinstance(value, str):
            self.path_edit.setText(value)
        backend = self.settings.value("backend", "Matplotlib")
        if isinstance(backend, str):
            index = self.backend_combo.findText(backend, QtCore.Qt.MatchFlag.MatchFixedString)
            if index >= 0:
                self.backend_combo.setCurrentIndex(index)
        mode = self.settings.value("mode", "files")
        if mode == "folder":
            self.folder_radio.setChecked(True)
        else:
            self.file_radio.setChecked(True)

    def _save_settings(self) -> None:
        self.settings.setValue("last_path", self.path_edit.text())
        self.settings.setValue("backend", self.backend_combo.currentText())
        self.settings.setValue("mode", "folder" if self.folder_radio.isChecked() else "files")
        self.settings.sync()

    # ------------------------------------------------------------------ file selection
    def _choose_input(self) -> None:
        if self.folder_radio.isChecked():
            directory = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Select folder with VSM files",
                self.path_edit.text() or str(Path.home()),
            )
            if directory:
                self.path_edit.setText(directory)
        else:
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self,
                "Select VSM files",
                self.path_edit.text() or str(Path.home()),
                "VSM data (*.VSM-Hys-Data);;All files (*)",
            )
            if files:
                self.path_edit.setText(";".join(files))
        self._save_settings()

    def _selected_paths(self) -> List[Path]:
        text = self.path_edit.text().strip()
        if not text:
            return []
        if self.folder_radio.isChecked():
            directory = Path(text)
            if not directory.is_dir():
                return []
            return sorted(p for p in directory.glob("*.VSM-Hys-Data") if p.is_file())
        return [Path(part) for part in text.split(";") if part]

    # ------------------------------------------------------------------ data loading
    def _load_measurements(self) -> None:
        self.measurements.clear()
        self.tab_widget.clear()
        self.log_view.clear()
        self.temperature_combo.blockSignals(True)
        self.temperature_combo.clear()
        self.temperature_combo.addItem("All temperatures", None)
        self.temperature_combo.blockSignals(False)
        self.plot_button.setEnabled(False)

        paths = self._selected_paths()
        if not paths:
            QtWidgets.QMessageBox.warning(self, "VSM Plot Explorer", "Select at least one VSM file to load.")
            return

        loaded = 0
        common_columns: Dict[str, int] | None = None
        for path in paths:
            if not path.exists():
                self._append_log(f"Skipping missing file: {path}")
                continue
            try:
                df = _read_vsm_file(path)
            except Exception as exc:
                self._append_log(f"Failed to parse {path.name}: {exc}")
                continue
            temperature = _parse_temperature(path)
            angle = _parse_angle(path)
            if temperature is None or angle is None:
                self._append_log(f"Could not parse metadata from filename: {path.name}")
                continue
            measurement = VSMMeasurement(path=path, temperature=temperature, angle=angle, data=df)
            self.measurements.append(measurement)
            loaded += 1
            column_set = {col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])}
            if common_columns is None:
                common_columns = {col: idx for idx, col in enumerate(df.columns) if col in column_set}
            else:
                common_columns = {col: idx for col, idx in common_columns.items() if col in column_set}

        if loaded == 0:
            QtWidgets.QMessageBox.information(self, "VSM Plot Explorer", "No valid VSM measurements were loaded.")
            return

        self.measurements.sort(key=lambda m: (m.temperature, m.angle))
        unique_temperatures = sorted({m.temperature for m in self.measurements})
        for temp in unique_temperatures:
            self.temperature_combo.addItem(f"{temp:g} °C", temp)

        self._append_log(f"Loaded {loaded} VSM measurements across {len(unique_temperatures)} temperatures.")

        if common_columns:
            self._populate_axis_combos(list(common_columns.keys()))
        else:
            self._populate_axis_combos(list(self.measurements[0].data.columns))
        self.plot_button.setEnabled(True)
        self._save_settings()

    def _populate_axis_combos(self, labels: List[str]) -> None:
        numeric_labels = [label for label in labels if label]
        preferred_x = [
            "Applied Field",
            "Applied Field [Oe]",
            "Applied Field For Plot",
        ]
        preferred_y = [
            "Signal parallel with sample",
            "Signal Magnitude",
            "Moment [emu]",
        ]
        def _choose(preferences: Iterable[str], combo: QtWidgets.QComboBox) -> None:
            for pref in preferences:
                matches = [label for label in numeric_labels if pref.lower() in label.lower()]
                if matches:
                    combo.setCurrentText(matches[0])
                    return
            if combo.count():
                combo.setCurrentIndex(0)

        for combo in (self.x_axis_combo, self.y_axis_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(numeric_labels)
            combo.blockSignals(False)
        _choose(preferred_x, self.x_axis_combo)
        _choose(preferred_y, self.y_axis_combo)

    # ------------------------------------------------------------------ plotting helpers
    def _generate_plots(self) -> None:
        if not self.measurements:
            QtWidgets.QMessageBox.warning(self, "VSM Plot Explorer", "Load VSM measurements first.")
            return
        x_axis = self.x_axis_combo.currentText()
        y_axis = self.y_axis_combo.currentText()
        if not x_axis or not y_axis:
            QtWidgets.QMessageBox.warning(self, "VSM Plot Explorer", "Select X and Y axes for plotting.")
            return

        target_temp = self.temperature_combo.currentData()
        groups: Dict[float, List[VSMMeasurement]] = {}
        for measurement in self.measurements:
            if target_temp is not None and measurement.temperature != target_temp:
                continue
            if x_axis not in measurement.data.columns or y_axis not in measurement.data.columns:
                self._append_log(f"Skipping {measurement.path.name} because it lacks the selected axes.")
                continue
            groups.setdefault(measurement.temperature, []).append(measurement)

        if not groups:
            QtWidgets.QMessageBox.information(
                self,
                "VSM Plot Explorer",
                "No measurements match the selected filters and axes.",
            )
            return

        backend_choice = self.backend_combo.currentText()
        render_matplotlib = wants_matplotlib(backend_choice)
        export_origin = wants_origin(backend_choice)

        self.tab_widget.clear()

        if render_matplotlib:
            self._render_matplotlib(groups, x_axis, y_axis)
        else:
            self.tab_widget.setVisible(False)

        if export_origin:
            self._export_origin(groups, x_axis, y_axis)

        if not render_matplotlib and not export_origin:
            self._append_log("No backend selected; nothing generated.")

    def _render_matplotlib(
        self,
        groups: Dict[float, List[VSMMeasurement]],
        x_axis: str,
        y_axis: str,
    ) -> None:
        self.tab_widget.setVisible(True)
        for temperature, measurements in sorted(groups.items()):
            fig = Figure(figsize=(11.5, 7.8))
            ax = fig.add_subplot(111)
            for measurement in sorted(measurements, key=lambda m: m.angle):
                subset = measurement.data[[x_axis, y_axis]].dropna()
                if subset.empty:
                    continue
                ax.plot(
                    subset[x_axis].astype(float).to_numpy(),
                    subset[y_axis].astype(float).to_numpy(),
                    label=f"{measurement.angle:g}°",
                )
            ax.set_xlabel(x_axis)
            ax.set_ylabel(y_axis)
            ax.set_title(f"{y_axis} vs {x_axis} at {temperature:g} °C")
            ax.legend(loc="best")
            ax.grid(True)
            fig.tight_layout()

            canvas = FigureCanvas(fig)
            tab = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(tab)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(canvas)
            self.tab_widget.addTab(tab, f"{temperature:g} °C")

        self._append_log("Finished generating Matplotlib hysteresis plots.")

    def _export_origin(
        self,
        groups: Dict[float, List[VSMMeasurement]],
        x_axis: str,
        y_axis: str,
    ) -> None:
        try:
            with origin_session() as op:
                schedule_origin_release()
                exported = 0
                for temperature, measurements in sorted(groups.items()):
                    valid = []
                    for measurement in sorted(measurements, key=lambda m: m.angle):
                        subset = measurement.data[[x_axis, y_axis]].dropna()
                        if subset.empty:
                            continue
                        valid.append((measurement, subset.astype(float)))
                    if not valid:
                        continue
                    try:
                        self._build_origin_group(op, temperature, valid, x_axis, y_axis)
                        exported += 1
                    except Exception as exc:
                        self._append_log(
                            f"Origin export failed for {temperature:g} °C: {exc}"
                        )
                if exported:
                    self._append_log(f"Sent {exported} temperature groups to Origin.")
                else:
                    self._append_log("No Origin plots were exported because all groups were empty.")
        except (ModuleNotFoundError, ImportError):
            self._append_log("OriginPro is not installed. Install originpro to enable Origin output.")
        except Exception as exc:
            self._append_log(f"Unexpected Origin error: {exc}")

    def _origin_book_name(self, temperature: float) -> str:
        label = f"VSM_{temperature:g}C"
        return "".join(ch if ch.isalnum() else "_" for ch in label)[:30]

    def _build_origin_group(
        self,
        origin_any: Any,
        temperature: float,
        entries: Sequence[Tuple[VSMMeasurement, pd.DataFrame]],
        x_axis: str,
        y_axis: str,
    ) -> None:
        book = origin_any.new_book('w', lname=self._origin_book_name(temperature))
        book.activate()

        graph = origin_any.new_graph(template='line')
        layer = graph[0] if graph else None
        if layer is None:
            return

        for index, (measurement, subset) in enumerate(entries):
            if index < len(book):
                sheet = book[index]
            else:
                sheet = book.add_sheet()
            sheet.name = f"a{measurement.angle:g}"
            sheet.from_df(subset)
            try:
                sheet.cols_axis('XY')
            except Exception:
                pass
            for col, label in enumerate((x_axis, y_axis)):
                try:
                    sheet.set_label(col, label)
                except Exception:
                    pass
            plot_obj = layer.add_plot(sheet, coly=1, colx=0, type='y')
            if plot_obj is not None:
                try:
                    plot_obj.legend = f"{measurement.angle:g}°"
                except Exception:
                    pass

        try:
            graph.activate()
        except Exception:
            pass

        safe_x = self._escape_origin_text(x_axis)
        safe_y = self._escape_origin_text(y_axis)
        safe_title = self._escape_origin_text(
            f"{y_axis} vs {x_axis} at {temperature:g} °C"
        )

        for command in (
            'page.antialias=1;',
            'layer -aa 1;',
            f'lab -xb "{safe_x}";',
            f'lab -yl "{safe_y}";',
            f'title -s "{safe_title}";',
            'legend;'
        ):
            try:
                origin_any.lt_exec(command)
            except Exception:
                pass

        try:
            layer.rescale()
        except Exception:
            pass

    def _escape_origin_text(self, text: str) -> str:
        return text.replace("\"", "''")

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.logger.info(message)


def main() -> QtWidgets.QWidget | None:  # pragma: no cover - launcher helper
    app = QtWidgets.QApplication.instance()
    created_app = False
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
        created_app = True
    ensure_app_theme(app)
    widget = VSMPlotter()
    widget.show()
    if created_app:
        app.exec()
        return None
    return widget


if __name__ == "__main__":  # pragma: no cover - manual execution
    main()
