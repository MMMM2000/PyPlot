from __future__ import annotations

import math
import os
import json
import re
import sys
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping, TextIO

from PyQt6 import QtCore, QtGui, QtWidgets

from app_help import show_help
from data_logging.naming_history import LineEditHistory

from matplotlib.figure import Figure

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except Exception:
    try:
        FigureCanvas = getattr(
            import_module("matplotlib.backends.backend_qt5agg"),
            "FigureCanvasQTAgg",
        )
    except Exception:  # pragma: no cover - optional backend fallback
        FigureCanvas = None  # type: ignore[assignment]

try:
    from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
except Exception:
    try:
        NavigationToolbar = getattr(
            import_module("matplotlib.backends.backend_qt5agg"),
            "NavigationToolbar2QT",
        )
    except Exception:  # pragma: no cover - optional backend fallback
        NavigationToolbar = None  # type: ignore[assignment]


def _normalize_path_for_checks(path: str) -> str:
    return path.replace("\\", "/").strip().lower()


def _looks_cache_redirect(path: str) -> bool:
    return "microwire_paddle_cache" in _normalize_path_for_checks(path)


def _default_download_dir() -> str:
    candidates: list[Path] = []

    user_profile = os.environ.get("USERPROFILE", "").strip()
    if user_profile:
        candidates.append(Path(user_profile) / "Downloads")

    try:
        qt_download = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.StandardLocation.DownloadLocation
        )
        if qt_download:
            candidates.append(Path(qt_download))
    except Exception:
        pass

    home = Path.home()
    candidates.extend((home / "Downloads", home / "downloads"))

    for candidate in candidates:
        if _looks_cache_redirect(str(candidate)):
            continue
        try:
            if candidate.exists() and candidate.is_dir():
                return str(candidate)
        except Exception:
            continue

    fallback = home / "Downloads"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(fallback)


DEFAULT_LOG_DIR = _default_download_dir()
DEFAULT_LOG_FILE_NAME = "FeSiBP 156_2 s1a 74mA"
GRAVITY_MS2 = 9.80665
LONG_NAMES = ("Displacement", "Load", "Strain", "Stress")
UNITS = ("mm", "g", "%", "MPa")
ZERO_TOLERANCE_G = 1e-9
MM_PER_POINT = 0.01
DISPLACEMENT_MODE_MM = "mm"
DISPLACEMENT_MODE_POINTS = "points"
START_MODE_FROM_ZERO = "start_0"
START_MODE_FROM_TEN = "start_10"
IDLE_TIMEOUT_DEFAULT_S = 55
START_POINTS_BY_MODE: dict[str, int] = {
    START_MODE_FROM_ZERO: 0,
    START_MODE_FROM_TEN: 10,
}
PLOT_VIEW_BOTH = "both"
PLOT_VIEW_RAW_ONLY = "raw_only"
PLOT_VIEW_DUAL_AXIS = "dual_axis"
UI_MAX_DECIMALS = 3
MICROMETER_DISPLAY_CYCLE = 50
MICROMETER_DISPLAY_STEP = 5
STRAIN_DIRECTION_TOLERANCE = 1e-9
LOADING_COLORS = ("#1f77b4", "#2ca02c", "#17becf", "#9467bd")
UNLOADING_COLORS = ("#d62728", "#ff7f0e", "#8c564b", "#e377c2")
HOLD_COLORS = ("#7f7f7f",)
BUILDER_PROJECT_KIND = "MicrowireDataBuilder"
PROJECT_DIAMETER_KEYS = ("d (µm)", "d (μm)", "d (um)", "diameter")
ANNEALING_FALLBACK_DIRS = (
    "sample_data/database_builder/current annealing data",
    "sample_data/current_annealing",
)
ANNEALING_HIGH_CURRENT_THRESHOLD_MA = 500.0
SUPERSCRIPT_MAP = str.maketrans({
    "-": "⁻",
    "+": "⁺",
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
})

# Keep references to windows created via main() to prevent collection when
# launched from the master launcher.
WINDOWS: list[QtWidgets.QWidget] = []


class ManualFileNameBuilderWidget(QtWidgets.QWidget):
    """Compact name builder for this logger (Stress + Custom only)."""

    def __init__(
        self,
        parent: QtWidgets.QWidget,
        target: QtWidgets.QLineEdit,
    ) -> None:
        super().__init__(parent)
        self.target = target
        self.settings = QtCore.QSettings("microwire", "manual_stress_strain_name_builder")
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        self.combo_format = QtWidgets.QComboBox(self)
        self.combo_format.addItems(["Stress", "Custom"])
        top_row.addWidget(self.combo_format, stretch=1)
        self.reset_btn = QtWidgets.QPushButton("Reset", self)
        self.reset_btn.setFixedWidth(92)
        top_row.addWidget(self.reset_btn, stretch=0)
        layout.addLayout(top_row)

        self.stacked = QtWidgets.QStackedWidget(self)
        layout.addWidget(self.stacked)

        stress = QtWidgets.QWidget(self)
        grid = QtWidgets.QGridLayout(stress)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.s_comp = QtWidgets.QLineEdit(self)
        self.s_comp.setText("FeSiBP")
        grid.addWidget(QtWidgets.QLabel("Composition:", stress), 0, 0)
        grid.addWidget(self.s_comp, 0, 1)

        self.s_sample = MicrowireLineEdit(self)
        grid.addWidget(QtWidgets.QLabel("Microwire:", stress), 0, 2)
        grid.addWidget(self.s_sample, 0, 3)

        self.s_number = QtWidgets.QLineEdit(self)
        self.s_number.setPlaceholderText("optional, e.g. s1")
        self.s_number.setText("s1")
        grid.addWidget(QtWidgets.QLabel("Sample number:", stress), 1, 0)
        grid.addWidget(self.s_number, 1, 1)

        self.s_current = QtWidgets.QLineEdit(self)
        self.s_current.setText("74mA")
        grid.addWidget(QtWidgets.QLabel("Current:", stress), 1, 2)
        grid.addWidget(self.s_current, 1, 3)

        self.s_notes = QtWidgets.QLineEdit(self)
        self.s_notes.setPlaceholderText("optional, e.g. no glass")
        grid.addWidget(QtWidgets.QLabel("Notes:", stress), 2, 0)
        grid.addWidget(self.s_notes, 2, 1, 1, 3)

        field_min_height = max(24, self.s_comp.sizeHint().height())
        for field in (
            self.s_comp,
            self.s_sample,
            self.s_number,
            self.s_current,
            self.s_notes,
        ):
            field.setMinimumHeight(field_min_height)

        self.stacked.addWidget(stress)
        self.stacked.addWidget(QtWidgets.QWidget(self))  # custom mode placeholder
        self.stacked.setMinimumHeight(field_min_height * 3 + 20)

        self.combo_format.currentIndexChanged.connect(self._handle_format_changed)
        for widget in (
            self.s_comp,
            self.s_sample,
            self.s_number,
            self.s_current,
            self.s_notes,
        ):
            if isinstance(widget, QtWidgets.QLineEdit):
                widget.textChanged.connect(self.update_name)
            elif isinstance(widget, QtWidgets.QComboBox):
                widget.currentIndexChanged.connect(self.update_name)
        self.reset_btn.clicked.connect(self.reset_defaults)

        self.load_settings()
        self._handle_format_changed(self.combo_format.currentIndex())

    def _handle_format_changed(self, idx: int) -> None:
        self.stacked.setCurrentIndex(idx)
        custom = self.combo_format.currentText() == "Custom"
        self.target.setReadOnly(not custom)
        if not custom:
            self.update_name()

    def _stress_name(self) -> str:
        parts: list[str] = []

        comp = self.s_comp.text().strip()
        if comp:
            parts.append(comp)

        wire = MicrowireLineEdit.to_filename_token(self.s_sample.text())
        if wire:
            parts.append(wire)

        sample_num = self.s_number.text().strip()
        if sample_num:
            parts.append(sample_num)

        current = self.s_current.text().strip()
        if current:
            parts.append(current)

        notes = self.s_notes.text().strip()
        if notes:
            parts.append(notes)

        return " ".join(parts)

    def update_name(self) -> None:
        if self.combo_format.currentText() != "Stress":
            return
        self.target.setText(self._stress_name())
        self.save_settings()

    def save_settings(self) -> None:
        settings = self.settings
        settings.setValue("format", self.combo_format.currentIndex())
        settings.setValue("s_comp", self.s_comp.text())
        settings.setValue("s_sample", self.s_sample.text())
        settings.setValue("s_number", self.s_number.text())
        settings.setValue("s_current", self.s_current.text())
        settings.setValue("s_notes", self.s_notes.text())
        settings.setValue("custom_text", self.target.text())

    def load_settings(self) -> None:
        settings = self.settings

        widgets: tuple[QtWidgets.QWidget, ...] = (
            self.combo_format,
            self.s_comp,
            self.s_sample,
            self.s_number,
            self.s_current,
            self.s_notes,
        )
        for widget in widgets:
            widget.blockSignals(True)

        self.combo_format.setCurrentIndex(int(settings.value("format", 0) or 0))
        self.s_comp.setText(settings.value("s_comp", "FeSiBP", type=str))
        self.s_sample.setText(settings.value("s_sample", "156_2", type=str))
        self.s_number.setText(settings.value("s_number", "s1", type=str))
        self.s_current.setText(settings.value("s_current", "74mA", type=str))
        self.s_notes.setText(settings.value("s_notes", "", type=str))
        self.target.setText(settings.value("custom_text", "", type=str))

        for widget in widgets:
            widget.blockSignals(False)

        if self.combo_format.currentText() == "Stress":
            self.update_name()

    def reset_defaults(self) -> None:
        self.settings.clear()
        self.load_settings()


class MicrowireLineEdit(QtWidgets.QLineEdit):
    """Microwire entry with fixed slash display, file-safe token conversion."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._normalizing = False
        self.setPlaceholderText("e.g. 11/1")
        self.setText("156/2")
        self.textEdited.connect(self._normalize_on_edit)

    @staticmethod
    def _split_parts(value: object) -> tuple[str, str]:
        text = str(value or "").strip().lower()
        if not text:
            return "", ""
        text = text.replace("\\", "/").replace("_", "/")
        text = re.sub(r"\s+", "", text)
        if "/" in text:
            left, right = text.split("/", 1)
        else:
            tokens = re.findall(r"\d+", text)
            if len(tokens) >= 2:
                left, right = tokens[0], tokens[1]
            elif len(tokens) == 1:
                left, right = tokens[0], ""
            else:
                left, right = "", ""
        return re.sub(r"\D", "", left), re.sub(r"\D", "", right)

    @classmethod
    def to_display_text(cls, value: object) -> str:
        left, right = cls._split_parts(value)
        return f"{left}/{right}" if (left or right) else "/"

    @classmethod
    def to_filename_token(cls, value: object) -> str:
        left, right = cls._split_parts(value)
        if left and right:
            return f"{left}_{right}"
        if left:
            return left
        if right:
            return right
        return ""

    def setText(self, text: str) -> None:  # type: ignore[override]
        super().setText(self.to_display_text(text))

    def _normalize_on_edit(self, _text: str) -> None:
        if self._normalizing:
            return
        normalized = self.to_display_text(self.text())
        if normalized == self.text():
            return
        cursor = self.cursorPosition()
        self._normalizing = True
        try:
            super().setText(normalized)
            self.setCursorPosition(min(cursor, len(normalized)))
        finally:
            self._normalizing = False


class ClickableLabel(QtWidgets.QLabel):
    clicked = QtCore.pyqtSignal()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class AnnealingPreviewDialog(QtWidgets.QDialog):
    """Non-modal preview of current annealing curves for a selected sample."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        title: str,
        series: list[dict[str, Any]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Current Annealing Preview - {title}")
        self.resize(980, 760)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        if FigureCanvas is None:
            label = QtWidgets.QLabel("Matplotlib Qt backend is unavailable.", self)
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label, stretch=1)
            return

        figure = Figure(figsize=(9.0, 7.0), tight_layout=True)
        canvas = FigureCanvas(figure)
        if NavigationToolbar is not None:
            toolbar = NavigationToolbar(canvas, self)
            layout.addWidget(toolbar)
        layout.addWidget(canvas, stretch=1)

        axis_high = figure.add_subplot(121)
        axis_low = figure.add_subplot(122)

        high_series = [entry for entry in series if str(entry.get("bucket", "")) == "high"]
        low_series = [entry for entry in series if str(entry.get("bucket", "")) != "high"]

        def _plot_bucket(
            axis: Any,
            bucket_series: list[dict[str, Any]],
            bucket_title: str,
            empty_text: str,
        ) -> None:
            for entry in bucket_series:
                label = str(entry.get("label", "Series"))
                currents = entry.get("currents")
                resistances = entry.get("resistances")
                if currents is None or resistances is None:
                    continue
                if len(resistances) == 0:
                    continue
                axis.plot(
                    currents,
                    resistances,
                    marker="o",
                    linewidth=1.2,
                    markersize=3.2,
                    label=label,
                )
            axis.set_title(bucket_title)
            axis.set_xlabel("Current (mA)")
            axis.set_ylabel("Resistance (Ohm)")
            axis.grid(True, alpha=0.35)
            if bucket_series:
                axis.legend(loc="best", fontsize=8)
            else:
                axis.text(
                    0.5,
                    0.5,
                    empty_text,
                    transform=axis.transAxes,
                    ha="center",
                    va="center",
                )

        _plot_bucket(axis_high, high_series, f"{title}\nHigh current", "No high-current curves")
        _plot_bucket(axis_low, low_series, f"{title}\nLow current", "No low-current curves")

        canvas.draw_idle()


class MainWindow(QtWidgets.QMainWindow):
    """Manual displacement/load logger with live stress-strain conversion."""

    def __init__(self, log_dir: str = DEFAULT_LOG_DIR) -> None:
        super().__init__()
        self.setWindowTitle("Manual Stress/Strain Logger")

        self.settings = QtCore.QSettings("microwire", "manual_stress_strain_logger")
        self.naming_history = LineEditHistory(
            QtCore.QSettings("microwire", "naming_history"),
            parent=self,
        )

        self.root_log_dir = log_dir
        self.log_dir = log_dir
        self.log_file: TextIO | None = None
        self.log_path: str | None = None
        self.logging_on = False

        self.displacements: list[float] = []  # always in mm
        self.loads: list[float] = []  # effective load (raw + offset), in g
        self.strains: list[float] = []
        self.stresses: list[float] = []

        self._strain_reference_disp: float | None = None
        self._preload_phase = True

        self._load_offset_g = 0.0
        self._pending_displacement_mm: float | None = None
        self._last_load_change_ts: float | None = None
        self._last_logged_load: float | None = None
        self._annealing_preview_windows: list[QtWidgets.QWidget] = []

        self._build_ui()
        self._bind_signals()
        self._restore_settings()

        self._idle_timer = QtCore.QTimer(self)
        self._idle_timer.setInterval(250)
        self._idle_timer.timeout.connect(self._update_idle_timer_label)
        self._idle_timer.start()

        self._sync_name_builder_history()
        self._refresh_plot()
        self._refresh_data_table()
        self._update_geometry_labels()
        self._update_status_labels()
        self._set_logging_controls(False)

    @staticmethod
    def header_rows() -> tuple[str, str]:
        return "\t".join(LONG_NAMES), "\t".join(UNITS)

    @staticmethod
    def update_reference_state(
        reference_mm: float | None,
        preload_phase: bool,
        displacement_mm: float,
        load_g: float,
        *,
        zero_tolerance_g: float = ZERO_TOLERANCE_G,
    ) -> tuple[float | None, bool]:
        """Track the last zero-load displacement until loading begins."""

        if preload_phase:
            if abs(load_g) <= zero_tolerance_g:
                reference_mm = displacement_mm
            else:
                if reference_mm is None:
                    reference_mm = displacement_mm
                preload_phase = False
        return reference_mm, preload_phase

    @staticmethod
    def strain_percent(
        displacement_mm: float,
        initial_length_mm: float,
        reference_mm: float | None,
    ) -> float | None:
        if reference_mm is None or initial_length_mm <= 0:
            return None
        return ((displacement_mm - reference_mm) / initial_length_mm) * 100.0

    @staticmethod
    def effective_initial_length_mm(
        initial_length_mm: float,
        start_offset_mm: float,
    ) -> float | None:
        effective = float(initial_length_mm) - max(0.0, float(start_offset_mm))
        if effective <= 0.0:
            return None
        return effective

    @staticmethod
    def stress_mpa_from_load_g(load_g: float, diameter_mm: float) -> float | None:
        if diameter_mm <= 0:
            return None
        area_mm2 = (math.pi * diameter_mm * diameter_mm) / 4.0
        if area_mm2 <= 0:
            return None
        force_n = load_g * GRAVITY_MS2 / 1000.0
        return force_n / area_mm2

    @staticmethod
    def effective_load_from_raw(raw_load_g: float, offset_g: float) -> float:
        return raw_load_g + offset_g

    @staticmethod
    def _snap_to_micrometer_step(value: float) -> int:
        snapped = int(round(value / MICROMETER_DISPLAY_STEP) * MICROMETER_DISPLAY_STEP)
        return snapped % MICROMETER_DISPLAY_CYCLE

    @classmethod
    def micrometer_display_from_points(
        cls,
        displacement_points: float,
        anchor_display: int,
        *,
        anchor_points: float = 0.0,
    ) -> int:
        return cls._snap_to_micrometer_step(
            float(anchor_display) + (float(displacement_points) - float(anchor_points))
        )

    @classmethod
    def micrometer_display_from_mm(
        cls,
        displacement_mm: float,
        anchor_display: int,
        *,
        anchor_points: float = 0.0,
        mm_per_point: float = MM_PER_POINT,
    ) -> int:
        points = float(displacement_mm) / float(mm_per_point)
        return cls.micrometer_display_from_points(
            points,
            anchor_display,
            anchor_points=anchor_points,
        )

    @staticmethod
    def should_insert_zero_anchor_point(
        *,
        existing_point_count: int,
        start_points: int,
        displacement_mm: float,
        mm_per_point: float = MM_PER_POINT,
    ) -> bool:
        if existing_point_count != 0 or start_points <= 0:
            return False
        start_mm = float(start_points) * float(mm_per_point)
        tolerance_mm = float(mm_per_point) * 0.5
        return displacement_mm >= (start_mm - tolerance_mm)

    @staticmethod
    def split_segments_by_strain_direction(
        strains: list[float],
        *,
        tolerance: float = STRAIN_DIRECTION_TOLERANCE,
    ) -> list[tuple[int, int, int]]:
        count = len(strains)
        if count == 0:
            return []
        if count == 1:
            return [(0, 0, 0)]

        segments: list[tuple[int, int, int]] = []
        start_index = 0
        current_direction = 0

        for index in range(1, count):
            delta = strains[index] - strains[index - 1]
            direction = 0
            if delta > tolerance:
                direction = 1
            elif delta < -tolerance:
                direction = -1

            if direction == 0:
                continue

            if current_direction == 0:
                current_direction = direction
                continue

            if direction != current_direction:
                segments.append((current_direction, start_index, max(start_index, index - 1)))
                start_index = max(0, index - 1)
                current_direction = direction

        segments.append((current_direction, start_index, count - 1))
        return segments

    @classmethod
    def build_segment_styles(
        cls,
        strains: list[float],
        *,
        tolerance: float = STRAIN_DIRECTION_TOLERANCE,
    ) -> list[tuple[int, int, int, str, str]]:
        segments = cls.split_segments_by_strain_direction(strains, tolerance=tolerance)
        styled: list[tuple[int, int, int, str, str]] = []
        loading_index = 0
        unloading_index = 0
        hold_index = 0

        for direction, start_index, end_index in segments:
            if direction > 0:
                loading_index += 1
                label = f"Loading {loading_index}"
                color = LOADING_COLORS[(loading_index - 1) % len(LOADING_COLORS)]
            elif direction < 0:
                unloading_index += 1
                label = f"Unloading {unloading_index}"
                color = UNLOADING_COLORS[(unloading_index - 1) % len(UNLOADING_COLORS)]
            else:
                hold_index += 1
                label = f"Hold {hold_index}"
                color = HOLD_COLORS[(hold_index - 1) % len(HOLD_COLORS)]
            styled.append((direction, start_index, end_index, label, color))
        return styled

    def _build_ui(self) -> None:
        self.central_widget = QtWidgets.QWidget(self)
        self.setCentralWidget(self.central_widget)

        root = QtWidgets.QHBoxLayout(self.central_widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        left_panel = QtWidgets.QWidget(self.central_widget)
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(10)

        try:
            screen = QtGui.QGuiApplication.primaryScreen()
            avail = (
                screen.availableGeometry()
                if screen is not None
                else QtCore.QRect(0, 0, 1440, 900)
            )
            fixed_width = min(660, max(520, int(avail.width() * 0.38)))
        except Exception:
            fixed_width = 560
        left_panel.setMinimumWidth(fixed_width)
        left_panel.setMaximumWidth(fixed_width)
        root.addWidget(left_panel, stretch=0)

        self.group_log = QtWidgets.QGroupBox("Logging")
        log_grid = QtWidgets.QGridLayout(self.group_log)
        log_grid.setColumnStretch(0, 0)
        log_grid.setColumnStretch(1, 1)
        log_grid.setColumnStretch(2, 0)
        log_grid.setColumnStretch(3, 0)

        log_grid.addWidget(QtWidgets.QLabel("Directory:"), 0, 0)
        self.lineEdit_log_dir = QtWidgets.QLineEdit()
        log_grid.addWidget(self.lineEdit_log_dir, 0, 1)
        self.pushButton_browse_dir = QtWidgets.QPushButton("Browse")
        self.pushButton_browse_dir.setFixedWidth(80)
        log_grid.addWidget(self.pushButton_browse_dir, 0, 2)
        self.pushButton_open_dir = QtWidgets.QPushButton("Open")
        self.pushButton_open_dir.setFixedWidth(70)
        log_grid.addWidget(self.pushButton_open_dir, 0, 3)

        log_grid.addWidget(QtWidgets.QLabel("File name:"), 1, 0)
        self.lineEdit_log_file = QtWidgets.QLineEdit()
        log_grid.addWidget(self.lineEdit_log_file, 1, 1)
        self.label_extension = QtWidgets.QLabel(".txt")
        self.label_extension.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        log_grid.addWidget(self.label_extension, 1, 2)

        start_stop_row = QtWidgets.QHBoxLayout()
        self.pushButton_start = QtWidgets.QPushButton("Start")
        self.pushButton_stop = QtWidgets.QPushButton("Stop")
        start_stop_row.addWidget(self.pushButton_start)
        start_stop_row.addWidget(self.pushButton_stop)
        start_stop_row.addStretch(1)
        self.checkBox_subdir = QtWidgets.QCheckBox("Use subfolder")
        self.checkBox_subdir.setToolTip(
            'Unsupported characters (<>:"/\\|?*) are replaced with underscores.'
        )
        start_stop_row.addWidget(self.checkBox_subdir)
        log_grid.addLayout(start_stop_row, 2, 0, 1, 4)

        self.label_session_status = QtWidgets.QLabel("Session: idle")
        self.label_session_status.setWordWrap(True)
        log_grid.addWidget(self.label_session_status, 3, 0, 1, 4)

        self.name_builder = ManualFileNameBuilderWidget(self.group_log, self.lineEdit_log_file)
        self.name_builder.setMinimumHeight(170)
        log_grid.addWidget(self.name_builder, 4, 0, 1, 4)
        log_grid.setRowStretch(4, 0)

        left_layout.addWidget(self.group_log)

        self.group_geometry = QtWidgets.QGroupBox("Sample Geometry")
        geom_form = QtWidgets.QFormLayout(self.group_geometry)
        geom_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        self.spin_l0_mm = QtWidgets.QDoubleSpinBox()
        self.spin_l0_mm.setDecimals(UI_MAX_DECIMALS)
        self.spin_l0_mm.setRange(0.001, 1_000_000.0)
        self.spin_l0_mm.setSingleStep(0.1)
        self.spin_l0_mm.setValue(20.0)
        self.spin_l0_mm.setSuffix(" mm")
        geom_form.addRow("Initial length L0:", self.spin_l0_mm)

        diameter_row = QtWidgets.QWidget()
        diameter_layout = QtWidgets.QHBoxLayout(diameter_row)
        diameter_layout.setContentsMargins(0, 0, 0, 0)
        diameter_layout.setSpacing(6)
        self.spin_diameter = QtWidgets.QDoubleSpinBox()
        self.spin_diameter.setDecimals(UI_MAX_DECIMALS)
        self.spin_diameter.setRange(0.001, 1_000_000.0)
        self.spin_diameter.setSingleStep(1.0)
        self.spin_diameter.setValue(30.0)
        self.spin_diameter.setMaximumWidth(180)
        self.combo_diameter_unit = QtWidgets.QComboBox()
        self.combo_diameter_unit.addItems(["um", "mm"])
        self.combo_diameter_unit.setFixedWidth(70)
        self.pushButton_autofill_diameter = QtWidgets.QPushButton("Auto-fill diameter")
        self.pushButton_autofill_diameter.setFixedWidth(130)
        diameter_layout.addWidget(self.spin_diameter, stretch=0)
        diameter_layout.addWidget(self.combo_diameter_unit)
        diameter_layout.addWidget(self.pushButton_autofill_diameter, stretch=0)
        diameter_layout.addStretch(1)
        geom_form.addRow("Diameter:", diameter_row)

        project_path_row = QtWidgets.QWidget(self.group_geometry)
        project_path_layout = QtWidgets.QHBoxLayout(project_path_row)
        project_path_layout.setContentsMargins(0, 0, 0, 0)
        project_path_layout.setSpacing(6)
        self.line_builder_project = QtWidgets.QLineEdit(self.group_geometry)
        self.line_builder_project.setReadOnly(True)
        self.line_builder_project.setPlaceholderText("Optional: connect .pydpj / .pypdj")
        project_path_layout.addWidget(self.line_builder_project, stretch=1)
        self.pushButton_connect_project = QtWidgets.QPushButton("Connect...")
        self.pushButton_connect_project.setFixedWidth(82)
        project_path_layout.addWidget(self.pushButton_connect_project, stretch=0)
        self.pushButton_show_annealing = QtWidgets.QPushButton("Show annealing")
        self.pushButton_show_annealing.setFixedWidth(112)
        project_path_layout.addWidget(self.pushButton_show_annealing, stretch=0)
        geom_form.addRow("DB project:", project_path_row)

        self.label_cross_section = QtWidgets.QLabel("N/A")
        geom_form.addRow("Area:", self.label_cross_section)

        self.label_reference = QtWidgets.QLabel("Waiting for first zero-load point")
        geom_form.addRow("Reference:", self.label_reference)

        left_layout.addWidget(self.group_geometry)

        self.group_input = QtWidgets.QGroupBox("Manual Input")
        input_form = QtWidgets.QFormLayout(self.group_input)
        input_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        self.combo_displacement_mode = QtWidgets.QComboBox(self)
        self.combo_displacement_mode.addItem("Millimeters", DISPLACEMENT_MODE_MM)
        self.combo_displacement_mode.addItem("Micrometer points (10^-2 mm)", DISPLACEMENT_MODE_POINTS)
        self.combo_start_mode = QtWidgets.QComboBox(self)
        self.combo_start_mode.addItem("Start from 0 points", START_MODE_FROM_ZERO)
        self.combo_start_mode.addItem("Start from 10 points", START_MODE_FROM_TEN)
        setup_row = QtWidgets.QWidget(self.group_input)
        setup_layout = QtWidgets.QHBoxLayout(setup_row)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(6)
        setup_layout.addWidget(QtWidgets.QLabel("Mode:", setup_row), stretch=0)
        setup_layout.addWidget(self.combo_displacement_mode, stretch=1)
        setup_layout.addWidget(QtWidgets.QLabel("Start:", setup_row), stretch=0)
        setup_layout.addWidget(self.combo_start_mode, stretch=1)
        input_form.addRow("Displacement:", setup_row)

        displacement_row = QtWidgets.QWidget(self.group_input)
        displacement_layout = QtWidgets.QHBoxLayout(displacement_row)
        displacement_layout.setContentsMargins(0, 0, 0, 0)
        displacement_layout.setSpacing(6)

        self.spin_displacement = QtWidgets.QDoubleSpinBox()
        self.spin_displacement.setRange(-1_000_000_000.0, 1_000_000_000.0)
        self.spin_displacement.setReadOnly(False)
        self.spin_displacement.setButtonSymbols(
            QtWidgets.QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        self.spin_displacement.lineEdit().setReadOnly(True)
        displacement_layout.addWidget(self.spin_displacement, stretch=1)

        self.line_micrometer_display = QtWidgets.QLineEdit(self.group_input)
        self.line_micrometer_display.setReadOnly(True)
        self.line_micrometer_display.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter
        )
        self.line_micrometer_display.setToolTip(
            "Current circular micrometer display (0..45, wraps by 5)."
        )
        self.line_micrometer_display.setFixedWidth(100)
        displacement_layout.addWidget(self.line_micrometer_display, stretch=0)

        input_form.addRow("Displacement:", displacement_row)

        self.label_micrometer_zero = QtWidgets.QLabel("Micrometer at d=10:")
        self.spin_micrometer_zero = QtWidgets.QSpinBox(self.group_input)
        self.spin_micrometer_zero.setRange(0, 45)
        self.spin_micrometer_zero.setSingleStep(5)
        self.spin_micrometer_zero.setValue(0)
        input_form.addRow(self.label_micrometer_zero, self.spin_micrometer_zero)

        self.spin_load_g = QtWidgets.QDoubleSpinBox()
        self.spin_load_g.setDecimals(UI_MAX_DECIMALS)
        self.spin_load_g.setRange(-1_000_000.0, 1_000_000.0)
        self.spin_load_g.setSingleStep(0.001)
        self.spin_load_g.setValue(0.0)
        self.spin_load_g.setSuffix(" g")

        load_row = QtWidgets.QWidget(self.group_input)
        load_layout = QtWidgets.QHBoxLayout(load_row)
        load_layout.setContentsMargins(0, 0, 0, 0)
        load_layout.setSpacing(6)
        load_layout.addWidget(self.spin_load_g, stretch=1)

        self.spin_idle_timeout_s = QtWidgets.QSpinBox(load_row)
        self.spin_idle_timeout_s.setRange(10, 600)
        self.spin_idle_timeout_s.setSingleStep(1)
        self.spin_idle_timeout_s.setValue(IDLE_TIMEOUT_DEFAULT_S)
        self.spin_idle_timeout_s.setSuffix(" s")
        self.spin_idle_timeout_s.setPrefix("T:")
        self.spin_idle_timeout_s.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.spin_idle_timeout_s.setFixedWidth(90)
        self.spin_idle_timeout_s.setToolTip("Scale timeout for countdown display.")
        load_layout.addWidget(self.spin_idle_timeout_s, stretch=0)

        self.label_idle_timer = ClickableLabel(f"{IDLE_TIMEOUT_DEFAULT_S}s left", load_row)
        self.label_idle_timer.setWordWrap(False)
        self.label_idle_timer.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter
        )
        self.label_idle_timer.setMinimumWidth(145)
        self.label_idle_timer.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.label_idle_timer.setToolTip("Click to reset countdown.")
        self.label_idle_timer.setStyleSheet(
            "font-weight: 700; padding: 3px 8px; border-radius: 4px; "
            "background-color: #5a5a5a; color: #ffffff;"
        )
        load_layout.addWidget(self.label_idle_timer, stretch=0)
        input_form.addRow("Load:", load_row)

        scale_row = QtWidgets.QHBoxLayout()
        self.pushButton_reset_displacement = QtWidgets.QPushButton("Reset d=10")
        scale_row.addWidget(self.pushButton_reset_displacement)
        self.pushButton_scale_rezero = QtWidgets.QPushButton("Scale Re-zero")
        scale_row.addWidget(self.pushButton_scale_rezero)
        scale_row.addStretch(1)
        input_form.addRow(scale_row)

        self.label_scale_offset = QtWidgets.QLabel("Offset: +0 g | Effective: +0 g")
        self.label_scale_offset.setWordWrap(False)
        input_form.addRow("", self.label_scale_offset)

        buttons_row = QtWidgets.QHBoxLayout()
        self.pushButton_add_point = QtWidgets.QPushButton("Add Point")
        self.pushButton_undo_point = QtWidgets.QPushButton("Undo Last")
        self.pushButton_clear_points = QtWidgets.QPushButton("Clear")
        buttons_row.addWidget(self.pushButton_add_point)
        buttons_row.addWidget(self.pushButton_undo_point)
        buttons_row.addWidget(self.pushButton_clear_points)
        input_form.addRow(buttons_row)

        self.label_point_count = QtWidgets.QLabel("Points: 0")
        input_form.addRow("", self.label_point_count)
        self.label_pending_displacement = QtWidgets.QLabel("Pending displacement: none")
        self.label_pending_displacement.setWordWrap(False)
        input_form.addRow("", self.label_pending_displacement)
        self.label_last_values = QtWidgets.QLabel("Last: N/A")
        self.label_last_values.setWordWrap(False)
        input_form.addRow("", self.label_last_values)

        left_layout.addWidget(self.group_input)

        left_layout.addStretch(1)

        right_panel = QtWidgets.QWidget(self.central_widget)
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        plot_controls = QtWidgets.QHBoxLayout()
        plot_controls.setContentsMargins(0, 0, 0, 0)
        plot_controls.setSpacing(6)
        plot_controls.addWidget(QtWidgets.QLabel("Plot view:", right_panel))
        self.combo_plot_view = QtWidgets.QComboBox(right_panel)
        self.combo_plot_view.addItem("Load vs Displacement + Stress vs Strain", PLOT_VIEW_BOTH)
        self.combo_plot_view.addItem("Load vs Displacement only", PLOT_VIEW_RAW_ONLY)
        self.combo_plot_view.addItem(
            "Dual-axis overlay (left/bottom + right/top)",
            PLOT_VIEW_DUAL_AXIS,
        )
        plot_controls.addWidget(self.combo_plot_view, stretch=1)
        plot_controls.addStretch(1)
        right_layout.addLayout(plot_controls)

        self.plot_frame = QtWidgets.QFrame(right_panel)
        self.plot_frame.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        plot_layout = QtWidgets.QVBoxLayout(self.plot_frame)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(4)

        self.figure: Figure | None = None
        self.canvas: QtWidgets.QWidget | None = None
        self._plot_toolbar: QtWidgets.QWidget | None = None
        self.ax_raw = None
        self.ax_derived = None
        self.ax_overlay_right = None
        self.ax_overlay_top = None
        self._plot_view_state = PLOT_VIEW_BOTH

        if FigureCanvas is not None:
            self.figure = Figure(figsize=(10, 5), tight_layout=True)
            canvas = FigureCanvas(self.figure)
            self.canvas = canvas
            if NavigationToolbar is not None:
                toolbar = NavigationToolbar(canvas, self)
                self._plot_toolbar = toolbar
                plot_layout.addWidget(toolbar)
            plot_layout.addWidget(canvas)
            self._rebuild_plot_axes(view_mode=PLOT_VIEW_BOTH)
        else:
            placeholder = QtWidgets.QLabel("Matplotlib Qt backend is unavailable.")
            placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            plot_layout.addWidget(placeholder, stretch=1)

        right_layout.addWidget(self.plot_frame, stretch=5)

        self.group_table = QtWidgets.QGroupBox("Logged Data", right_panel)
        table_layout = QtWidgets.QVBoxLayout(self.group_table)
        table_layout.setContentsMargins(8, 8, 8, 8)
        table_layout.setSpacing(6)
        self.table_data = QtWidgets.QTableWidget(0, 5, self.group_table)
        self.table_data.setHorizontalHeaderLabels(
            [
                "Displacement (mm)",
                "Micrometer (0..45)",
                "Load (g)",
                "Strain (%)",
                "Stress (MPa)",
            ]
        )
        self.table_data.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_data.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table_data.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table_data.verticalHeader().setVisible(False)
        self.table_data.setMinimumHeight(190)
        self.table_data.setAlternatingRowColors(True)
        self.table_data.horizontalHeader().setStretchLastSection(True)
        self.table_data.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_data.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_data.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_data.horizontalHeader().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_data.horizontalHeader().setSectionResizeMode(
            4, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        table_layout.addWidget(self.table_data)
        right_layout.addWidget(self.group_table, stretch=2)

        root.addWidget(right_panel, stretch=1)

        self._install_menu()

        try:
            self.setMinimumSize(fixed_width + 760, 680)
        except Exception:
            self.setMinimumSize(1220, 680)

    def _bind_signals(self) -> None:
        self.pushButton_browse_dir.clicked.connect(self.choose_log_dir)
        self.pushButton_open_dir.clicked.connect(self.open_log_dir)
        self.pushButton_start.clicked.connect(self.start_session)
        self.pushButton_stop.clicked.connect(self.stop_session)

        self.pushButton_add_point.clicked.connect(self.add_point)
        self.pushButton_undo_point.clicked.connect(self.undo_last_point)
        self.pushButton_clear_points.clicked.connect(self.clear_points)
        self.pushButton_reset_displacement.clicked.connect(self.handle_reset_displacement)
        self.pushButton_scale_rezero.clicked.connect(self.handle_scale_rezero)
        self.pushButton_connect_project.clicked.connect(self.choose_builder_project)
        self.pushButton_autofill_diameter.clicked.connect(self.autofill_diameter_from_project)
        self.pushButton_show_annealing.clicked.connect(self.show_project_annealing_graphs)

        self.lineEdit_log_file.returnPressed.connect(self.start_session)
        self.spin_displacement.lineEdit().returnPressed.connect(self._handle_displacement_enter)
        self.spin_load_g.lineEdit().returnPressed.connect(self._handle_load_enter)

        self.spin_l0_mm.valueChanged.connect(self._handle_geometry_changed)
        self.spin_diameter.valueChanged.connect(self._handle_geometry_changed)
        self.combo_diameter_unit.currentIndexChanged.connect(self._handle_geometry_changed)
        self.combo_displacement_mode.currentIndexChanged.connect(
            self._handle_displacement_mode_changed
        )
        self.combo_start_mode.currentIndexChanged.connect(self._handle_start_mode_changed)
        self.combo_plot_view.currentIndexChanged.connect(self._handle_plot_view_changed)
        self.spin_displacement.valueChanged.connect(self._update_micrometer_display)
        self.spin_micrometer_zero.valueChanged.connect(self._handle_micrometer_anchor_changed)
        self.spin_load_g.valueChanged.connect(self._update_status_labels)
        self.spin_idle_timeout_s.valueChanged.connect(self._update_idle_timer_label)
        self.label_idle_timer.clicked.connect(self.reset_idle_timer)

    def _install_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        if file_menu is not None:
            open_folder_action = file_menu.addAction("Open &Folder...")
            if open_folder_action is not None:
                open_folder_action.triggered.connect(self.choose_log_dir)
            file_menu.addSeparator()
            close_action = file_menu.addAction("&Close")
            if close_action is not None:
                close_action.triggered.connect(self.close)

        help_menu = menu_bar.addMenu("&Help")
        if help_menu is not None:
            help_action = help_menu.addAction("View &Help")
            if help_action is not None:
                help_action.triggered.connect(
                    lambda: show_help("logger_manual_stress_strain", self)
                )

    def _sync_name_builder_history(self) -> None:
        self.naming_history.register("composition", getattr(self.name_builder, "s_comp", None))
        self.naming_history.register("microwire", getattr(self.name_builder, "s_sample", None))

    def _select_displacement_mode(self, mode: str) -> None:
        index = self.combo_displacement_mode.findData(mode)
        if index < 0:
            index = self.combo_displacement_mode.findData(DISPLACEMENT_MODE_MM)
        if index < 0:
            index = 0
        self.combo_displacement_mode.setCurrentIndex(index)

    def _is_valid_dir(self, path_text: str) -> bool:
        if not path_text:
            return False
        try:
            path = Path(path_text)
            return path.exists() and path.is_dir()
        except Exception:
            return False

    def _restore_settings(self) -> None:
        stored_dir = self.settings.value("log_dir", self.log_dir, type=str).strip()
        if not stored_dir or _looks_cache_redirect(stored_dir) or not self._is_valid_dir(stored_dir):
            stored_dir = DEFAULT_LOG_DIR
        self.log_dir = stored_dir
        self.root_log_dir = stored_dir
        self.lineEdit_log_dir.setText(stored_dir)

        self.lineEdit_log_file.setText(
            self.settings.value("log_file", DEFAULT_LOG_FILE_NAME, type=str)
        )
        self.checkBox_subdir.setChecked(bool(int(self.settings.value("use_subdir", 1) or 0)))

        self.spin_l0_mm.setValue(float(self.settings.value("l0_mm", 20.0)))
        self.spin_diameter.setValue(float(self.settings.value("diameter_value", 30.0)))
        self.combo_diameter_unit.setCurrentIndex(
            int(self.settings.value("diameter_unit", 0) or 0)
        )
        builder_project_path = self.settings.value("builder_project_path", "", type=str).strip()
        self.line_builder_project.setText(builder_project_path)
        self.line_builder_project.setToolTip(builder_project_path)

        stored_mode = self.settings.value("displacement_mode", DISPLACEMENT_MODE_MM, type=str)
        self.combo_displacement_mode.blockSignals(True)
        self._select_displacement_mode(stored_mode)
        self.combo_displacement_mode.blockSignals(False)

        stored_start_mode = self.settings.value("start_mode", START_MODE_FROM_TEN, type=str)
        self.combo_start_mode.blockSignals(True)
        self._select_start_mode(stored_start_mode)
        self.combo_start_mode.blockSignals(False)
        self._apply_start_mode_ui()

        self._apply_displacement_mode(self._current_displacement_mode(), preserve_mm=False)
        disp_mm = float(self.settings.value("input_disp_mm", self._start_displacement_mm()))
        self._set_displacement_input_from_mm(disp_mm)

        self.spin_load_g.setValue(float(self.settings.value("input_load_raw", 0.0)))
        idle_timeout = int(self.settings.value("idle_timeout_s", IDLE_TIMEOUT_DEFAULT_S) or IDLE_TIMEOUT_DEFAULT_S)
        self.spin_idle_timeout_s.setValue(max(10, min(600, idle_timeout)))
        self._load_offset_g = float(self.settings.value("load_offset_g", 0.0) or 0.0)
        zero_display = int(self.settings.value("micrometer_zero_display", 0) or 0)
        self.spin_micrometer_zero.setValue(self._snap_to_micrometer_step(zero_display))

        stored_plot_view = self.settings.value("plot_view", PLOT_VIEW_BOTH, type=str)
        self.combo_plot_view.blockSignals(True)
        self._select_plot_view(stored_plot_view)
        self.combo_plot_view.blockSignals(False)
        self._rebuild_plot_axes(view_mode=self._current_plot_view())

    def _save_settings(self) -> None:
        dir_text = self.lineEdit_log_dir.text().strip() or self.log_dir
        if _looks_cache_redirect(dir_text) or not self._is_valid_dir(dir_text):
            dir_text = DEFAULT_LOG_DIR
        self.settings.setValue("log_dir", dir_text)
        self.settings.setValue("log_file", self.lineEdit_log_file.text().strip())
        self.settings.setValue("use_subdir", 1 if self.checkBox_subdir.isChecked() else 0)
        self.settings.setValue("l0_mm", self.spin_l0_mm.value())
        self.settings.setValue("diameter_value", self.spin_diameter.value())
        self.settings.setValue("diameter_unit", self.combo_diameter_unit.currentIndex())
        self.settings.setValue("builder_project_path", self.line_builder_project.text().strip())
        self.settings.setValue("displacement_mode", self._current_displacement_mode())
        self.settings.setValue("start_mode", self._current_start_mode())
        self.settings.setValue("input_disp_mm", self._displacement_mm_from_input())
        self.settings.setValue("input_load_raw", self.spin_load_g.value())
        self.settings.setValue("idle_timeout_s", self.spin_idle_timeout_s.value())
        self.settings.setValue("load_offset_g", self._load_offset_g)
        self.settings.setValue("micrometer_zero_display", self.spin_micrometer_zero.value())
        self.settings.setValue("plot_view", self._current_plot_view())

    def _current_displacement_mode(self) -> str:
        mode = self.combo_displacement_mode.currentData()
        if isinstance(mode, str):
            return mode
        return DISPLACEMENT_MODE_MM

    def _select_start_mode(self, mode: str) -> None:
        index = self.combo_start_mode.findData(mode)
        if index < 0:
            index = self.combo_start_mode.findData(START_MODE_FROM_TEN)
        if index < 0:
            index = 0
        self.combo_start_mode.setCurrentIndex(index)

    def _current_start_mode(self) -> str:
        mode = self.combo_start_mode.currentData()
        if isinstance(mode, str):
            return mode
        return START_MODE_FROM_TEN

    def _current_start_points(self) -> int:
        return START_POINTS_BY_MODE.get(self._current_start_mode(), 10)

    def _start_displacement_mm(self) -> float:
        return float(self._current_start_points()) * MM_PER_POINT

    def _effective_l0_mm(self) -> float | None:
        return self.effective_initial_length_mm(
            float(self.spin_l0_mm.value()),
            self._start_displacement_mm(),
        )

    def _apply_start_mode_ui(self) -> None:
        start_points = self._current_start_points()
        self.pushButton_reset_displacement.setText(f"Reset d={start_points}")
        self.label_micrometer_zero.setText(f"Micrometer at d={start_points}:")

    def _select_plot_view(self, mode: str) -> None:
        index = self.combo_plot_view.findData(mode)
        if index < 0:
            index = self.combo_plot_view.findData(PLOT_VIEW_BOTH)
        if index < 0:
            index = 0
        self.combo_plot_view.setCurrentIndex(index)

    def _current_plot_view(self) -> str:
        mode = self.combo_plot_view.currentData()
        if isinstance(mode, str):
            return mode
        return PLOT_VIEW_BOTH

    def _plot_view_shows_derived(self) -> bool:
        return self._current_plot_view() in (PLOT_VIEW_BOTH, PLOT_VIEW_DUAL_AXIS)

    def _apply_displacement_mode(self, mode: str, *, preserve_mm: bool = True) -> None:
        current_mm = self._displacement_mm_from_input() if preserve_mm else self._start_displacement_mm()

        if mode == DISPLACEMENT_MODE_POINTS:
            self.spin_displacement.setDecimals(0)
            self.spin_displacement.setSingleStep(10.0)
            self.spin_displacement.setValue(0.0)
            self.spin_displacement.setSuffix(" x10⁻² mm")
        else:
            self.spin_displacement.setDecimals(UI_MAX_DECIMALS)
            self.spin_displacement.setSingleStep(0.01)
            self.spin_displacement.setValue(0.0)
            self.spin_displacement.setSuffix(" mm")

        show_points_controls = mode == DISPLACEMENT_MODE_POINTS
        self.line_micrometer_display.setVisible(show_points_controls)
        self.label_micrometer_zero.setVisible(show_points_controls)
        self.spin_micrometer_zero.setVisible(show_points_controls)

        if preserve_mm:
            self._set_displacement_input_from_mm(current_mm)
        self._update_micrometer_display()

    def _set_displacement_input_from_mm(self, value_mm: float) -> None:
        if self._current_displacement_mode() == DISPLACEMENT_MODE_POINTS:
            self.spin_displacement.setValue(value_mm / MM_PER_POINT)
            return
        self.spin_displacement.setValue(value_mm)

    def _displacement_mm_from_input(self) -> float:
        input_value = float(self.spin_displacement.value())
        if self._current_displacement_mode() == DISPLACEMENT_MODE_POINTS:
            return input_value * MM_PER_POINT
        return input_value

    def _displacement_display_values(self) -> tuple[list[float], str]:
        if self._current_displacement_mode() == DISPLACEMENT_MODE_POINTS:
            values = [value_mm / MM_PER_POINT for value_mm in self.displacements]
            return values, r"Displacement (x10$^{-2}$ mm)"
        return list(self.displacements), "Displacement (mm)"

    def _display_displacement_to_mm(self, value: float) -> float:
        if self._current_displacement_mode() == DISPLACEMENT_MODE_POINTS:
            return float(value) * MM_PER_POINT
        return float(value)

    def _handle_displacement_mode_changed(self, _index: int) -> None:
        self._apply_displacement_mode(self._current_displacement_mode(), preserve_mm=True)
        self._pending_displacement_mm = None
        self._update_status_labels()
        self._refresh_plot()

    def _handle_start_mode_changed(self, _index: int) -> None:
        self._apply_start_mode_ui()
        self._pending_displacement_mm = None
        if not self.displacements:
            self.handle_reset_displacement()
        else:
            self._recalculate_derived(persist=True)
            self._update_micrometer_display()

    def _handle_micrometer_anchor_changed(self, _value: int) -> None:
        self._update_micrometer_display()
        self._refresh_data_table()

    def _handle_plot_view_changed(self, _index: int) -> None:
        self._rebuild_plot_axes(view_mode=self._current_plot_view())
        self._refresh_plot()

    def _rebuild_plot_axes(self, *, view_mode: str) -> None:
        if self.figure is None:
            return
        self.figure.clear()
        self.ax_overlay_right = None
        self.ax_overlay_top = None
        if view_mode == PLOT_VIEW_BOTH:
            self.ax_raw = self.figure.add_subplot(121)
            self.ax_derived = self.figure.add_subplot(122)
        elif view_mode == PLOT_VIEW_RAW_ONLY:
            self.ax_raw = self.figure.add_subplot(111)
            self.ax_derived = None
        else:
            self.ax_raw = self.figure.add_subplot(111)
            self.ax_overlay_right = self.ax_raw.twinx()
            self.ax_overlay_top = self.ax_raw.twiny()
            self.ax_derived = self.ax_overlay_top
        self._plot_view_state = view_mode

    def _update_micrometer_display(self) -> None:
        if not hasattr(self, "line_micrometer_display"):
            return
        if self._current_displacement_mode() != DISPLACEMENT_MODE_POINTS:
            self.line_micrometer_display.clear()
            return

        points_value = float(self.spin_displacement.value())
        zero_display = int(self.spin_micrometer_zero.value())
        displayed = self.micrometer_display_from_points(
            points_value,
            zero_display,
            anchor_points=float(self._current_start_points()),
        )
        self.line_micrometer_display.setText(str(displayed))

    def _project_dialog_start_path(self) -> str:
        selected = self.line_builder_project.text().strip()
        if selected:
            selected_path = Path(selected)
            if selected_path.exists():
                if selected_path.is_file():
                    return str(selected_path.parent)
                return str(selected_path)
        log_dir = self.lineEdit_log_dir.text().strip() or self.root_log_dir
        if self._is_valid_dir(log_dir):
            return log_dir
        return str(Path.home())

    @staticmethod
    def _normalize_comp_token(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _normalize_microwire_token(value: object) -> str:
        text = str(value or "").strip().lower()
        text = text.replace("\\", "/").replace("_", "/")
        text = re.sub(r"\s+", "", text)
        return text.strip("/")

    @staticmethod
    def _to_positive_float(value: object) -> float | None:
        if isinstance(value, (int, float)):
            parsed = float(value)
        elif isinstance(value, str):
            cleaned = value.strip().replace(",", ".")
            if not cleaned:
                return None
            try:
                parsed = float(cleaned)
            except ValueError:
                return None
        else:
            return None
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _parse_project_key(key_value: object) -> tuple[str, str]:
        if not isinstance(key_value, str):
            return "", ""
        parts = [part.strip() for part in key_value.split("|")]
        if len(parts) < 3:
            return "", ""
        composition = parts[0]
        microwire = "/".join(part for part in parts[1:] if part)
        return composition, microwire

    @classmethod
    def extract_project_diameter_candidates(
        cls,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, Mapping):
            return []

        sections_payload = payload.get("sections")
        section_items: list[tuple[str, Mapping[str, Any]]] = []
        if isinstance(sections_payload, Mapping):
            microscope_payload = sections_payload.get("microscope")
            if isinstance(microscope_payload, Mapping):
                section_items.append(("microscope", microscope_payload))
            for section_name, section_payload in sections_payload.items():
                if section_name == "microscope":
                    continue
                if isinstance(section_payload, Mapping):
                    section_items.append((str(section_name), section_payload))
        else:
            section_items.append(("root", payload))

        candidates: list[dict[str, Any]] = []
        for section_name, section_payload in section_items:
            rows_payload = section_payload.get("rows")
            if not isinstance(rows_payload, (list, tuple)):
                continue
            for row_payload in rows_payload:
                if not isinstance(row_payload, Mapping):
                    continue
                diameter_um: float | None = None
                for key in PROJECT_DIAMETER_KEYS:
                    diameter_um = cls._to_positive_float(row_payload.get(key))
                    if diameter_um is not None:
                        break
                if diameter_um is None:
                    continue

                key_comp, key_wire = cls._parse_project_key(row_payload.get("_key"))
                composition = str(
                    row_payload.get("Composition")
                    or row_payload.get("composition")
                    or key_comp
                    or ""
                ).strip()
                microwire = str(
                    row_payload.get("Microwire")
                    or row_payload.get("microwire")
                    or key_wire
                    or ""
                ).strip()

                candidates.append(
                    {
                        "section": section_name,
                        "composition": composition,
                        "microwire": microwire,
                        "diameter_um": diameter_um,
                        "composition_norm": cls._normalize_comp_token(composition),
                        "microwire_norm": cls._normalize_microwire_token(microwire),
                    }
                )
        return candidates

    @classmethod
    def choose_project_diameter_candidate(
        cls,
        candidates: list[dict[str, Any]],
        *,
        composition_hint: str,
        microwire_hint: str,
    ) -> int | None:
        if not candidates:
            return None

        comp_norm = cls._normalize_comp_token(composition_hint)
        wire_norm = cls._normalize_microwire_token(microwire_hint)

        def _matching_indices(
            *,
            require_comp: bool,
            require_wire: bool,
        ) -> list[int]:
            indices: list[int] = []
            for index, candidate in enumerate(candidates):
                candidate_comp = cls._normalize_comp_token(candidate.get("composition"))
                candidate_wire = cls._normalize_microwire_token(candidate.get("microwire"))
                if require_comp and candidate_comp != comp_norm:
                    continue
                if require_wire and candidate_wire != wire_norm:
                    continue
                indices.append(index)
            return indices

        if comp_norm and wire_norm:
            matches = _matching_indices(require_comp=True, require_wire=True)
            if matches:
                return matches[0]

        if comp_norm:
            matches = _matching_indices(require_comp=True, require_wire=False)
            if len(matches) == 1:
                return matches[0]

        if wire_norm:
            matches = _matching_indices(require_comp=False, require_wire=True)
            if len(matches) == 1:
                return matches[0]

        if len(candidates) == 1:
            return 0
        return None

    @classmethod
    def extract_project_annealing_candidates(
        cls,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(payload, Mapping):
            return []
        sections_payload = payload.get("sections")
        if not isinstance(sections_payload, Mapping):
            return []
        annealing_payload = sections_payload.get("annealing")
        if not isinstance(annealing_payload, Mapping):
            return []
        rows_payload = annealing_payload.get("rows")
        if not isinstance(rows_payload, (list, tuple)):
            return []

        candidates: list[dict[str, Any]] = []
        for row_payload in rows_payload:
            if not isinstance(row_payload, Mapping):
                continue

            raw_sources = row_payload.get("_sources")
            if not isinstance(raw_sources, (list, tuple)):
                continue
            sources = [str(source).strip() for source in raw_sources if str(source).strip()]
            if not sources:
                continue

            composition = str(
                row_payload.get("Composition")
                or row_payload.get("composition")
                or ""
            ).strip()
            microwire = str(
                row_payload.get("Microwire")
                or row_payload.get("microwire")
                or ""
            ).strip()

            candidates.append(
                {
                    "composition": composition,
                    "microwire": microwire,
                    "sources": sources,
                    "group_key": str(row_payload.get("_group_key") or ""),
                    "composition_norm": cls._normalize_comp_token(composition),
                    "microwire_norm": cls._normalize_microwire_token(microwire),
                }
            )
        return candidates

    @classmethod
    def choose_project_annealing_candidate(
        cls,
        candidates: list[dict[str, Any]],
        *,
        composition_hint: str,
        microwire_hint: str,
    ) -> int | None:
        if not candidates:
            return None

        comp_norm = cls._normalize_comp_token(composition_hint)
        wire_norm = cls._normalize_microwire_token(microwire_hint)

        def _matching_indices(
            *,
            require_comp: bool,
            require_wire: bool,
        ) -> list[int]:
            indices: list[int] = []
            for index, candidate in enumerate(candidates):
                candidate_comp = cls._normalize_comp_token(candidate.get("composition"))
                candidate_wire = cls._normalize_microwire_token(candidate.get("microwire"))
                if require_comp and candidate_comp != comp_norm:
                    continue
                if require_wire and candidate_wire != wire_norm:
                    continue
                indices.append(index)
            return indices

        if comp_norm and wire_norm:
            matches = _matching_indices(require_comp=True, require_wire=True)
            if len(matches) == 1:
                return matches[0]
            if matches:
                return matches[0]

        if comp_norm:
            matches = _matching_indices(require_comp=True, require_wire=False)
            if len(matches) == 1:
                return matches[0]

        if wire_norm:
            matches = _matching_indices(require_comp=False, require_wire=True)
            if len(matches) == 1:
                return matches[0]

        if len(candidates) == 1:
            return 0
        return None

    @staticmethod
    def _source_basename(source_path: str) -> str:
        normalized = str(source_path).replace("\\", "/").strip()
        if not normalized:
            return ""
        return Path(normalized).name

    @staticmethod
    def annealing_setpoint_from_source(source_path: str) -> float | None:
        stem = Path(str(source_path).replace("\\", "/")).stem
        text = stem.replace(",", ".")
        match = re.search(r"(\d+(?:\.\d+)?)\s*mA\b", text, flags=re.IGNORECASE)
        if match is None:
            return None
        try:
            return abs(float(match.group(1)))
        except ValueError:
            return None

    @classmethod
    def annealing_current_bucket(
        cls,
        *,
        source_path: str,
        currents_mA: Any,
    ) -> str:
        setpoint = cls.annealing_setpoint_from_source(source_path)
        if setpoint is None:
            try:
                setpoint = max(abs(float(value)) for value in currents_mA)
            except Exception:
                setpoint = 0.0
        return "high" if float(setpoint) >= ANNEALING_HIGH_CURRENT_THRESHOLD_MA else "low"

    @classmethod
    def filter_annealing_sources_by_sample(
        cls,
        sources: list[str],
        sample_hint: str,
    ) -> list[str]:
        sample = str(sample_hint or "").strip().lower()
        if not sample:
            return list(sources)

        variants = {
            sample,
            sample.replace("/", "_"),
            sample.replace("_", "/"),
            re.sub(r"\s+", "", sample),
        }
        matches: list[str] = []
        for source in sources:
            stem = Path(str(source).replace("\\", "/")).stem.lower()
            stem_dense = re.sub(r"\s+", "", stem)
            if any(token and (token in stem or token in stem_dense) for token in variants):
                matches.append(source)
        return matches if matches else list(sources)

    def _project_root_dir(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _resolve_annealing_source_file(
        self,
        *,
        project_path: str,
        source_path: str,
    ) -> Path | None:
        source_text = str(source_path).strip()
        if not source_text:
            return None

        direct = Path(source_text)
        try:
            if direct.exists() and direct.is_file():
                return direct
        except Exception:
            pass

        normalized = source_text.replace("\\", "/")
        normalized_path = Path(normalized)
        try:
            if normalized_path.exists() and normalized_path.is_file():
                return normalized_path
        except Exception:
            pass

        basename = self._source_basename(source_text)
        if not basename:
            return None

        project_dir = Path(project_path).resolve().parent if project_path else Path.cwd()
        repo_root = self._project_root_dir()
        candidates = [
            project_dir / basename,
            project_dir / "current annealing data" / basename,
            project_dir.parent / "current annealing data" / basename,
        ]
        for rel_dir in ANNEALING_FALLBACK_DIRS:
            candidates.append(repo_root / rel_dir / basename)

        for candidate in candidates:
            try:
                if candidate.exists() and candidate.is_file():
                    return candidate
            except Exception:
                continue

        search_roots = [repo_root / rel_dir for rel_dir in ANNEALING_FALLBACK_DIRS]
        for root in search_roots:
            try:
                if not root.exists():
                    continue
                match = next(root.rglob(basename), None)
                if match is not None and match.is_file():
                    return match
            except Exception:
                continue

        return None

    def _annealing_candidate_label(self, candidate: Mapping[str, Any]) -> str:
        composition = str(candidate.get("composition", "")).strip() or "?"
        microwire = str(candidate.get("microwire", "")).strip() or "?"
        source_count = len(candidate.get("sources", []) or [])
        return f"{composition} | {microwire} | {source_count} source file(s)"

    def _load_annealing_preview_series(
        self,
        *,
        project_path: str,
        sources: list[str],
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        try:
            from plotting.plugins.current_annealing import core as annealing_core
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Annealing preview",
                f"Failed to import current annealing parser:\n{exc}",
            )
            return [], [], []

        loaded_series: list[dict[str, Any]] = []
        missing_sources: list[str] = []
        failed_sources: list[str] = []

        for source in sources:
            resolved = self._resolve_annealing_source_file(
                project_path=project_path,
                source_path=source,
            )
            if resolved is None:
                missing_sources.append(source)
                continue
            try:
                frame = annealing_core.load_file(str(resolved))
            except Exception as exc:
                failed_sources.append(f"{self._source_basename(source)}: {exc}")
                continue

            try:
                currents = frame["I_mA"].to_numpy(dtype=float)
                resistances = frame["R_Ohm"].to_numpy(dtype=float)
            except Exception as exc:
                failed_sources.append(f"{self._source_basename(source)}: {exc}")
                continue

            if len(currents) == 0:
                continue

            bucket = self.annealing_current_bucket(
                source_path=source,
                currents_mA=currents,
            )
            setpoint_mA = self.annealing_setpoint_from_source(source)
            label = Path(str(source).replace("\\", "/")).stem
            if setpoint_mA is None:
                try:
                    setpoint_mA = max(abs(float(value)) for value in currents)
                except Exception:
                    setpoint_mA = None
            if setpoint_mA is not None:
                label = f"{label} ({self._format_ui(setpoint_mA)} mA)"

            loaded_series.append(
                {
                    "label": label,
                    "currents": currents,
                    "resistances": resistances,
                    "path": str(resolved),
                    "bucket": bucket,
                }
            )

        return loaded_series, missing_sources, failed_sources

    def _project_candidate_label(self, candidate: Mapping[str, Any]) -> str:
        composition = str(candidate.get("composition", "")).strip() or "?"
        microwire = str(candidate.get("microwire", "")).strip() or "?"
        section = str(candidate.get("section", "")).strip() or "section"
        diameter_um = float(candidate.get("diameter_um", 0.0) or 0.0)
        return (
            f"{composition} | {microwire} | "
            f"d={self._format_ui(diameter_um)} um ({section})"
        )

    def _load_builder_project_payload(self, project_path: str) -> Mapping[str, Any] | None:
        path_obj = Path(project_path)
        try:
            payload = json.loads(path_obj.read_text(encoding="utf-8"))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Project read error",
                f"Failed to open project file:\n{exc}",
            )
            return None
        if not isinstance(payload, Mapping):
            QtWidgets.QMessageBox.warning(
                self,
                "Project format",
                "Project file does not contain a valid JSON object.",
            )
            return None

        kind = payload.get("kind")
        if isinstance(kind, str) and kind and kind != BUILDER_PROJECT_KIND:
            QtWidgets.QMessageBox.warning(
                self,
                "Project format",
                f"Unsupported project kind '{kind}'. Expected '{BUILDER_PROJECT_KIND}'.",
            )
            return None
        return payload

    def choose_builder_project(self) -> None:
        start_path = self._project_dialog_start_path()
        filters = "Microwire Project (*.pydpj *.pypdj);;All files (*)"
        selected_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select microwire project",
            start_path,
            filters,
        )
        if not selected_path:
            return
        self.line_builder_project.setText(selected_path)
        self.line_builder_project.setToolTip(selected_path)
        self.autofill_diameter_from_project()

    def autofill_diameter_from_project(self) -> None:
        project_path = self.line_builder_project.text().strip()
        if not project_path:
            self.choose_builder_project()
            return

        payload = self._load_builder_project_payload(project_path)
        if payload is None:
            return

        candidates = self.extract_project_diameter_candidates(payload)
        if not candidates:
            QtWidgets.QMessageBox.warning(
                self,
                "Diameter not found",
                "No valid diameter values were found in this project file.",
            )
            return

        composition_hint = ""
        microwire_hint = ""
        if isinstance(getattr(self.name_builder, "s_comp", None), QtWidgets.QLineEdit):
            composition_hint = self.name_builder.s_comp.text().strip()
        if isinstance(getattr(self.name_builder, "s_sample", None), QtWidgets.QLineEdit):
            microwire_hint = self.name_builder.s_sample.text().strip()

        candidate_index = self.choose_project_diameter_candidate(
            candidates,
            composition_hint=composition_hint,
            microwire_hint=microwire_hint,
        )

        if candidate_index is None:
            labels = [self._project_candidate_label(candidate) for candidate in candidates]
            selected_label, confirmed = QtWidgets.QInputDialog.getItem(
                self,
                "Select diameter",
                "Choose the diameter record to apply:",
                labels,
                0,
                False,
            )
            if not confirmed:
                return
            try:
                candidate_index = labels.index(selected_label)
            except ValueError:
                candidate_index = None
        if candidate_index is None:
            return

        selected = candidates[candidate_index]
        diameter_um = self._to_positive_float(selected.get("diameter_um"))
        if diameter_um is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Diameter not found",
                "Selected record does not contain a valid positive diameter.",
            )
            return

        unit_index = self.combo_diameter_unit.findText("um")
        if unit_index >= 0:
            self.combo_diameter_unit.setCurrentIndex(unit_index)
        self.spin_diameter.setValue(diameter_um)

    def show_project_annealing_graphs(self) -> None:
        project_path = self.line_builder_project.text().strip()
        if not project_path:
            QtWidgets.QMessageBox.information(
                self,
                "Annealing preview",
                "Connect a .pydpj project first.",
            )
            return

        payload = self._load_builder_project_payload(project_path)
        if payload is None:
            return

        candidates = self.extract_project_annealing_candidates(payload)
        if not candidates:
            QtWidgets.QMessageBox.warning(
                self,
                "Annealing preview",
                "No annealing source files were found in this project.",
            )
            return

        composition_hint = ""
        microwire_hint = ""
        sample_hint = ""
        if isinstance(getattr(self.name_builder, "s_comp", None), QtWidgets.QLineEdit):
            composition_hint = self.name_builder.s_comp.text().strip()
        if isinstance(getattr(self.name_builder, "s_sample", None), QtWidgets.QLineEdit):
            microwire_hint = self.name_builder.s_sample.text().strip()
        if isinstance(getattr(self.name_builder, "s_number", None), QtWidgets.QLineEdit):
            sample_hint = self.name_builder.s_number.text().strip()

        candidate_index = self.choose_project_annealing_candidate(
            candidates,
            composition_hint=composition_hint,
            microwire_hint=microwire_hint,
        )
        if candidate_index is None:
            labels = [self._annealing_candidate_label(candidate) for candidate in candidates]
            selected_label, confirmed = QtWidgets.QInputDialog.getItem(
                self,
                "Select annealing sample",
                "Choose which project row to preview:",
                labels,
                0,
                False,
            )
            if not confirmed:
                return
            try:
                candidate_index = labels.index(selected_label)
            except ValueError:
                candidate_index = None
        if candidate_index is None:
            return

        candidate = candidates[candidate_index]
        sources = [
            str(source).strip()
            for source in candidate.get("sources", [])
            if str(source).strip()
        ]
        if not sources:
            QtWidgets.QMessageBox.warning(
                self,
                "Annealing preview",
                "No source files matched this selection.",
            )
            return

        series, missing_sources, failed_sources = self._load_annealing_preview_series(
            project_path=project_path,
            sources=sources,
        )
        if not series:
            details: list[str] = []
            if missing_sources:
                details.append("Missing source files:")
                details.extend(f"  - {self._source_basename(path)}" for path in missing_sources[:12])
            if failed_sources:
                details.append("Failed to parse:")
                details.extend(f"  - {item}" for item in failed_sources[:12])
            detail_text = "\n".join(details) if details else "No valid annealing curves found."
            QtWidgets.QMessageBox.warning(
                self,
                "Annealing preview",
                detail_text,
            )
            return

        high_count = sum(1 for entry in series if str(entry.get("bucket", "")) == "high")
        low_count = len(series) - high_count

        title_parts = [
            str(candidate.get("composition", "")).strip(),
            str(candidate.get("microwire", "")).strip(),
        ]
        title = " ".join(part for part in title_parts if part) or "Current annealing preview"

        dialog = AnnealingPreviewDialog(self, title, series)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(
            lambda _obj=None, dlg=dialog: self._annealing_preview_windows.remove(dlg)
            if dlg in self._annealing_preview_windows
            else None
        )
        self._annealing_preview_windows.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        if missing_sources or failed_sources:
            message_lines: list[str] = []
            if missing_sources:
                message_lines.append(
                    f"Some files were not found ({len(missing_sources)})."
                )
            if failed_sources:
                message_lines.append(
                    f"Some files failed to parse ({len(failed_sources)})."
                )
            QtWidgets.QMessageBox.information(
                self,
                "Annealing preview",
                " ".join(message_lines),
            )
        if high_count == 0 or low_count == 0:
            missing_bucket = "high-current" if high_count == 0 else "low-current"
            QtWidgets.QMessageBox.information(
                self,
                "Annealing preview",
                f"Loaded curves, but no {missing_bucket} dataset was detected.",
            )

    def _current_format(self) -> str:
        return self.name_builder.combo_format.currentText().strip()

    def _record_name_history(self) -> None:
        if self._current_format() != "Stress":
            return
        for key, widget in (
            ("composition", getattr(self.name_builder, "s_comp", None)),
            ("microwire", getattr(self.name_builder, "s_sample", None)),
        ):
            if isinstance(widget, QtWidgets.QLineEdit):
                self.naming_history.remember(key, widget.text())

    def _set_logging_controls(self, active: bool) -> None:
        self.pushButton_start.setEnabled(not active)
        self.pushButton_stop.setEnabled(active)

        self.lineEdit_log_file.setEnabled(not active)
        self.checkBox_subdir.setEnabled(not active)
        self.name_builder.setEnabled(not active)
        self.spin_l0_mm.setEnabled(not active)
        self.spin_diameter.setEnabled(not active)
        self.combo_diameter_unit.setEnabled(not active)
        self.pushButton_connect_project.setEnabled(not active)
        self.pushButton_autofill_diameter.setEnabled(not active)

        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        can_edit = self.logging_on
        has_points = bool(self.displacements)
        self.pushButton_add_point.setEnabled(can_edit)
        self.pushButton_undo_point.setEnabled(can_edit and has_points)
        self.pushButton_clear_points.setEnabled(can_edit and has_points)
        self.pushButton_scale_rezero.setEnabled(can_edit)

    def _diameter_mm(self) -> float:
        diameter = float(self.spin_diameter.value())
        if self.combo_diameter_unit.currentText() == "um":
            return diameter / 1000.0
        return diameter

    def _cross_section_area_mm2(self) -> float | None:
        diameter_mm = self._diameter_mm()
        if diameter_mm <= 0:
            return None
        area_mm2 = (math.pi * diameter_mm * diameter_mm) / 4.0
        if area_mm2 <= 0:
            return None
        return area_mm2

    def _effective_load_now(self) -> float:
        raw = float(self.spin_load_g.value())
        return self.effective_load_from_raw(raw, self._load_offset_g)

    def _update_geometry_labels(self) -> None:
        area_mm2 = self._cross_section_area_mm2()
        if area_mm2 is None:
            self.label_cross_section.setText("Invalid diameter")
        else:
            self.label_cross_section.setText(f"{self._format_area_mm2(area_mm2)} mm²")

        if self._strain_reference_disp is None:
            self.label_reference.setText("Waiting for first zero-load point")
        else:
            state = "preload" if self._preload_phase else "locked"
            self.label_reference.setText(
                f"{self._format_ui(self._strain_reference_disp)} mm ({state})"
            )

    def _update_status_labels(self) -> None:
        self.label_point_count.setText(f"Points: {len(self.displacements)}")

        if self._pending_displacement_mm is None:
            self.label_pending_displacement.setText("Pending displacement: none")
        else:
            self.label_pending_displacement.setText(
                f"Pending displacement: {self._format_ui(self._pending_displacement_mm)} mm"
            )

        effective = self._effective_load_now()
        self.label_scale_offset.setText(
            f"Offset: {self._format_ui(self._load_offset_g, signed=True)} g | "
            f"Effective: {self._format_ui(effective, signed=True)} g"
        )

        if self.strains and self.stresses:
            self.label_last_values.setText(
                f"Last: strain={self._format_ui(self.strains[-1])} %, "
                f"stress={self._format_ui(self.stresses[-1])} MPa"
            )
        else:
            self.label_last_values.setText("Last: N/A")

        if self.logging_on and self.log_path:
            self.label_session_status.setText(f"Session: recording to {self.log_path}")
        else:
            self.label_session_status.setText("Session: idle")
        self._update_idle_timer_label()

    @staticmethod
    def _format_ui(value: float, *, signed: bool = False) -> str:
        decimals = UI_MAX_DECIMALS
        text = f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
        text = text.rstrip("0").rstrip(".")
        if text in ("", "+", "-"):
            text = "+0" if signed else "0"
        if text in ("-0", "+-0"):
            text = "+0" if signed else "0"
        return text

    @staticmethod
    def _format_area_mm2(value: float) -> str:
        abs_value = abs(float(value))
        if abs_value <= 0.0:
            return "0"
        if abs_value < 0.001:
            scientific = f"{value:.3e}"
            mantissa_text, exponent_text = scientific.split("e")
            exponent = int(exponent_text)
            exponent_sup = str(exponent).translate(SUPERSCRIPT_MAP)
            return f"{mantissa_text}x10{exponent_sup}"
        return MainWindow._format_ui(value)

    def _set_idle_indicator(self, text: str, fg: str, bg: str) -> None:
        self.label_idle_timer.setText(text)
        self.label_idle_timer.setStyleSheet(
            "font-weight: 700; padding: 3px 8px; border-radius: 4px; "
            f"background-color: {bg}; color: {fg};"
        )

    def _idle_timeout_seconds(self) -> int:
        if not hasattr(self, "spin_idle_timeout_s"):
            return IDLE_TIMEOUT_DEFAULT_S
        return max(1, int(self.spin_idle_timeout_s.value()))

    @staticmethod
    def countdown_seconds_left(
        timeout_s: int,
        *,
        last_change_ts: float | None,
        now_ts: float | None = None,
    ) -> int:
        timeout = max(0, int(timeout_s))
        if last_change_ts is None:
            return timeout
        current_ts = time.monotonic() if now_ts is None else float(now_ts)
        elapsed = max(0, int(current_ts - float(last_change_ts)))
        return max(0, timeout - elapsed)

    def _update_idle_timer_label(self) -> None:
        if not hasattr(self, "label_idle_timer"):
            return
        timeout_s = self._idle_timeout_seconds()
        if not self.logging_on:
            self._set_idle_indicator(f"{timeout_s}s left", "#f0f0f0", "#5a5a5a")
            return

        remaining = self.countdown_seconds_left(
            timeout_s,
            last_change_ts=self._last_load_change_ts,
        )

        text = f"{remaining}s left"
        red_limit = max(5, min(15, timeout_s // 6))
        amber_limit = max(10, min(25, timeout_s // 3))
        if remaining <= red_limit:
            self._set_idle_indicator(text, "#ffffff", "#d7263d")
        elif remaining <= amber_limit:
            self._set_idle_indicator(text, "#1b1b1b", "#ffb347")
        else:
            self._set_idle_indicator(text, "#ffffff", "#228b22")

    def reset_idle_timer(self) -> None:
        if not self.logging_on:
            return
        self._last_load_change_ts = time.monotonic()
        self._update_idle_timer_label()

    def _refresh_data_table(self) -> None:
        if not hasattr(self, "table_data"):
            return
        anchor_display = int(self.spin_micrometer_zero.value()) if hasattr(self, "spin_micrometer_zero") else 0
        anchor_points = float(self._current_start_points())
        self.table_data.setRowCount(len(self.displacements))
        for row, (displacement, load, strain, stress) in enumerate(
            zip(self.displacements, self.loads, self.strains, self.stresses)
        ):
            micrometer_display = self.micrometer_display_from_mm(
                displacement,
                anchor_display,
                anchor_points=anchor_points,
            )
            values = (
                self._format_value(displacement),
                str(micrometer_display),
                self._format_value(load),
                self._format_ui(strain),
                self._format_ui(stress),
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(
                    int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                )
                self.table_data.setItem(row, column, item)
        if self.table_data.rowCount() > 0:
            self.table_data.scrollToBottom()

    def _apply_plot_theme(self) -> tuple[str, str, str]:
        palette = self.palette()
        figure_bg = palette.color(QtGui.QPalette.ColorRole.Window).name()
        axis_bg = palette.color(QtGui.QPalette.ColorRole.Base).name()
        axis_fg = palette.color(QtGui.QPalette.ColorRole.Text).name()
        if self.figure is not None:
            self.figure.patch.set_facecolor(figure_bg)
        return axis_bg, axis_fg, figure_bg

    def _refresh_plot(self) -> None:
        if self.figure is None or self.canvas is None:
            return
        view_mode = self._current_plot_view()
        if self.ax_raw is None or view_mode != self._plot_view_state:
            self._rebuild_plot_axes(view_mode=view_mode)
        if self.ax_raw is None:
            return
        if view_mode == PLOT_VIEW_DUAL_AXIS:
            self._refresh_plot_dual_axis()
        else:
            self._refresh_plot_standard(show_derived=(view_mode == PLOT_VIEW_BOTH))
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _style_axis(
        self,
        axis: object,
        *,
        axis_bg: str,
        axis_fg: str,
        show_grid: bool,
        clear_first: bool = True,
    ) -> None:
        if clear_first:
            axis.clear()
        axis.set_facecolor(axis_bg)
        axis.tick_params(colors=axis_fg)
        for spine in axis.spines.values():
            spine.set_color(axis_fg)
        if show_grid:
            axis.grid(True, color=(0.35, 0.35, 0.35, 0.35))
        else:
            axis.grid(False)

    @staticmethod
    def _format_single_axis_coord(axis: object, x: float | None, y: float | None) -> str:
        x_text = "???" if x is None else axis.format_xdata(x)
        y_text = "???" if y is None else axis.format_ydata(y)
        return f"(x, y) = ({x_text}, {y_text})"

    @staticmethod
    def _map_linear_value(
        value: float | None,
        *,
        src_min: float,
        src_max: float,
        dst_min: float,
        dst_max: float,
    ) -> float | None:
        if value is None:
            return None
        denom = src_max - src_min
        if not math.isfinite(denom) or abs(denom) < 1e-15:
            return None
        ratio = (float(value) - src_min) / denom
        return dst_min + ratio * (dst_max - dst_min)

    def _format_dual_axis_pair_text(
        self,
        *,
        raw_x: float | None,
        raw_y: float | None,
        strain_x: float | None,
        stress_y: float | None,
    ) -> str:
        if (
            self.ax_raw is None
            or self.ax_overlay_top is None
            or self.ax_overlay_right is None
        ):
            return ""
        raw_x_text = "???" if raw_x is None else self.ax_raw.format_xdata(raw_x)
        raw_y_text = "???" if raw_y is None else self.ax_raw.format_ydata(raw_y)
        strain_x_text = (
            "???" if strain_x is None else self.ax_overlay_top.format_xdata(strain_x)
        )
        stress_y_text = (
            "???" if stress_y is None else self.ax_overlay_right.format_ydata(stress_y)
        )
        return (
            f"L/D (x, y) = ({raw_x_text}, {raw_y_text}) | "
            f"S/S (x, y) = ({strain_x_text}, {stress_y_text})"
        )

    def _format_dual_axis_coord_from_raw(
        self,
        raw_x: float | None,
        raw_y: float | None,
    ) -> str:
        if (
            self.ax_raw is None
            or self.ax_overlay_top is None
            or self.ax_overlay_right is None
        ):
            return ""
        raw_x_min, raw_x_max = self.ax_raw.get_xlim()
        strain_x_min, strain_x_max = self.ax_overlay_top.get_xlim()
        raw_y_min, raw_y_max = self.ax_raw.get_ylim()
        stress_y_min, stress_y_max = self.ax_overlay_right.get_ylim()
        strain_x = self._map_linear_value(
            raw_x,
            src_min=raw_x_min,
            src_max=raw_x_max,
            dst_min=strain_x_min,
            dst_max=strain_x_max,
        )
        stress_y = self._map_linear_value(
            raw_y,
            src_min=raw_y_min,
            src_max=raw_y_max,
            dst_min=stress_y_min,
            dst_max=stress_y_max,
        )
        return self._format_dual_axis_pair_text(
            raw_x=raw_x,
            raw_y=raw_y,
            strain_x=strain_x,
            stress_y=stress_y,
        )

    def _format_dual_axis_coord_from_top(
        self,
        strain_x: float | None,
        load_y: float | None,
    ) -> str:
        if (
            self.ax_raw is None
            or self.ax_overlay_top is None
            or self.ax_overlay_right is None
        ):
            return ""
        strain_x_min, strain_x_max = self.ax_overlay_top.get_xlim()
        raw_x_min, raw_x_max = self.ax_raw.get_xlim()
        raw_y_min, raw_y_max = self.ax_raw.get_ylim()
        stress_y_min, stress_y_max = self.ax_overlay_right.get_ylim()
        raw_x = self._map_linear_value(
            strain_x,
            src_min=strain_x_min,
            src_max=strain_x_max,
            dst_min=raw_x_min,
            dst_max=raw_x_max,
        )
        stress_y = self._map_linear_value(
            load_y,
            src_min=raw_y_min,
            src_max=raw_y_max,
            dst_min=stress_y_min,
            dst_max=stress_y_max,
        )
        return self._format_dual_axis_pair_text(
            raw_x=raw_x,
            raw_y=load_y,
            strain_x=strain_x,
            stress_y=stress_y,
        )

    def _format_dual_axis_coord_from_right(
        self,
        displacement_x: float | None,
        stress_y: float | None,
    ) -> str:
        if (
            self.ax_raw is None
            or self.ax_overlay_top is None
            or self.ax_overlay_right is None
        ):
            return ""
        raw_x_min, raw_x_max = self.ax_raw.get_xlim()
        strain_x_min, strain_x_max = self.ax_overlay_top.get_xlim()
        stress_y_min, stress_y_max = self.ax_overlay_right.get_ylim()
        raw_y_min, raw_y_max = self.ax_raw.get_ylim()
        strain_x = self._map_linear_value(
            displacement_x,
            src_min=raw_x_min,
            src_max=raw_x_max,
            dst_min=strain_x_min,
            dst_max=strain_x_max,
        )
        raw_y = self._map_linear_value(
            stress_y,
            src_min=stress_y_min,
            src_max=stress_y_max,
            dst_min=raw_y_min,
            dst_max=raw_y_max,
        )
        return self._format_dual_axis_pair_text(
            raw_x=displacement_x,
            raw_y=raw_y,
            strain_x=strain_x,
            stress_y=stress_y,
        )

    def _sync_dual_axis_limits(self) -> None:
        if (
            self.ax_raw is None
            or self.ax_overlay_top is None
            or self.ax_overlay_right is None
        ):
            return

        raw_x_min, raw_x_max = self.ax_raw.get_xlim()
        raw_y_min, raw_y_max = self.ax_raw.get_ylim()

        reference_mm = self._strain_reference_disp
        l0_mm = self._effective_l0_mm()
        if l0_mm is None:
            self.ax_overlay_top.set_xlim(raw_x_min, raw_x_max)
            self.ax_overlay_right.set_ylim(raw_y_min, raw_y_max)
            return
        strain_min = self.strain_percent(
            self._display_displacement_to_mm(raw_x_min),
            l0_mm,
            reference_mm,
        )
        strain_max = self.strain_percent(
            self._display_displacement_to_mm(raw_x_max),
            l0_mm,
            reference_mm,
        )
        if strain_min is None or strain_max is None:
            self.ax_overlay_top.set_xlim(raw_x_min, raw_x_max)
        else:
            self.ax_overlay_top.set_xlim(strain_min, strain_max)

        diameter_mm = self._diameter_mm()
        stress_min = self.stress_mpa_from_load_g(raw_y_min, diameter_mm)
        stress_max = self.stress_mpa_from_load_g(raw_y_max, diameter_mm)
        if stress_min is None or stress_max is None:
            self.ax_overlay_right.set_ylim(raw_y_min, raw_y_max)
        else:
            self.ax_overlay_right.set_ylim(stress_min, stress_max)

    def _refresh_plot_standard(self, *, show_derived: bool) -> None:
        if self.ax_raw is None:
            return

        axis_bg, axis_fg, _ = self._apply_plot_theme()
        self._style_axis(self.ax_raw, axis_bg=axis_bg, axis_fg=axis_fg, show_grid=True)
        if show_derived and self.ax_derived is not None:
            self._style_axis(self.ax_derived, axis_bg=axis_bg, axis_fg=axis_fg, show_grid=True)

        x_values, x_label = self._displacement_display_values()

        self.ax_raw.set_title("Load vs Displacement", color=axis_fg)
        self.ax_raw.set_xlabel(x_label, color=axis_fg)
        self.ax_raw.set_ylabel("Load (g)", color=axis_fg)

        if show_derived and self.ax_derived is not None:
            self.ax_derived.set_title("Stress vs Strain", color=axis_fg)
            self.ax_derived.set_xlabel("Strain (%)", color=axis_fg)
            self.ax_derived.set_ylabel("Stress (MPa)", color=axis_fg)

        if x_values:
            styles = self.build_segment_styles(self.strains)
            plotted = False
            for _direction, start_index, end_index, label, color in styles:
                if start_index >= len(x_values):
                    continue
                end = min(end_index, len(x_values) - 1, len(self.loads) - 1)
                if end < start_index:
                    continue
                self.ax_raw.plot(
                    x_values[start_index : end + 1],
                    self.loads[start_index : end + 1],
                    color=color,
                    marker="o",
                    linewidth=1.6,
                    markersize=4,
                    label=label,
                )
                plotted = True
            if plotted:
                self.ax_raw.legend(loc="best", fontsize=8)
        else:
            self.ax_raw.text(
                0.5,
                0.5,
                "No data yet",
                transform=self.ax_raw.transAxes,
                ha="center",
                va="center",
                color=axis_fg,
            )

        if show_derived and self.ax_derived is not None and self.strains:
            styles = self.build_segment_styles(self.strains)
            plotted = False
            for _direction, start_index, end_index, label, color in styles:
                if start_index >= len(self.strains):
                    continue
                end = min(end_index, len(self.strains) - 1, len(self.stresses) - 1)
                if end < start_index:
                    continue
                self.ax_derived.plot(
                    self.strains[start_index : end + 1],
                    self.stresses[start_index : end + 1],
                    color=color,
                    marker="o",
                    linewidth=1.6,
                    markersize=4,
                    label=label,
                )
                plotted = True
            if plotted:
                self.ax_derived.legend(loc="best", fontsize=8)
        elif show_derived and self.ax_derived is not None:
            self.ax_derived.text(
                0.5,
                0.5,
                "No data yet",
                transform=self.ax_derived.transAxes,
                ha="center",
                va="center",
                color=axis_fg,
            )

    def _refresh_plot_dual_axis(self) -> None:
        if (
            self.ax_raw is None
            or self.ax_overlay_right is None
            or self.ax_overlay_top is None
        ):
            return

        axis_bg, axis_fg, _ = self._apply_plot_theme()
        self._style_axis(self.ax_raw, axis_bg=axis_bg, axis_fg=axis_fg, show_grid=True)
        self._style_axis(
            self.ax_overlay_right,
            axis_bg=axis_bg,
            axis_fg=axis_fg,
            show_grid=False,
        )
        self._style_axis(
            self.ax_overlay_top,
            axis_bg=axis_bg,
            axis_fg=axis_fg,
            show_grid=False,
        )

        self.ax_overlay_right.patch.set_alpha(0.0)
        self.ax_overlay_top.patch.set_alpha(0.0)
        self.ax_overlay_right.xaxis.set_visible(False)
        self.ax_overlay_top.yaxis.set_visible(False)
        self.ax_raw.spines["top"].set_visible(False)
        self.ax_raw.spines["right"].set_visible(False)
        self.ax_raw.xaxis.set_label_position("bottom")
        self.ax_raw.yaxis.set_label_position("left")
        self.ax_raw.tick_params(
            axis="x",
            colors=axis_fg,
            bottom=True,
            labelbottom=True,
            top=False,
            labeltop=False,
        )
        self.ax_raw.tick_params(
            axis="y",
            colors=axis_fg,
            left=True,
            labelleft=True,
            right=False,
            labelright=False,
        )
        self.ax_overlay_top.xaxis.set_label_position("top")
        self.ax_overlay_top.xaxis.tick_top()
        self.ax_overlay_top.tick_params(
            axis="x",
            colors=axis_fg,
            top=True,
            labeltop=True,
            bottom=False,
            labelbottom=False,
        )
        self.ax_overlay_right.yaxis.set_label_position("right")
        self.ax_overlay_right.yaxis.tick_right()
        self.ax_overlay_right.tick_params(
            axis="y",
            colors=axis_fg,
            right=True,
            labelright=True,
            left=False,
            labelleft=False,
        )
        self.ax_overlay_top.spines["left"].set_visible(False)
        self.ax_overlay_top.spines["right"].set_visible(False)
        self.ax_overlay_top.spines["bottom"].set_visible(False)
        self.ax_overlay_right.spines["left"].set_visible(False)
        self.ax_overlay_right.spines["bottom"].set_visible(False)
        self.ax_overlay_right.spines["top"].set_visible(False)

        x_values, x_label = self._displacement_display_values()
        self.ax_raw.set_title("Load vs Displacement + Stress vs Strain", color=axis_fg)
        self.ax_raw.set_xlabel(x_label, color=axis_fg)
        self.ax_raw.set_ylabel("Load (g)", color=axis_fg)
        self.ax_overlay_top.set_xlabel("Strain (%)", color=axis_fg, labelpad=10)
        self.ax_overlay_right.set_ylabel("Stress (MPa)", color=axis_fg, labelpad=10)
        self.ax_raw.format_coord = (
            lambda x, y: self._format_dual_axis_coord_from_raw(x, y)
        )
        self.ax_overlay_top.format_coord = (
            lambda x, y: self._format_dual_axis_coord_from_top(x, y)
        )
        self.ax_overlay_right.format_coord = (
            lambda x, y: self._format_dual_axis_coord_from_right(x, y)
        )

        styles = self.build_segment_styles(self.strains)

        raw_plotted = False
        if x_values:
            for _direction, start_index, end_index, label, color in styles:
                if start_index >= len(x_values):
                    continue
                end = min(end_index, len(x_values) - 1, len(self.loads) - 1)
                if end < start_index:
                    continue
                self.ax_raw.plot(
                    x_values[start_index : end + 1],
                    self.loads[start_index : end + 1],
                    color=color,
                    marker="o",
                    linewidth=1.6,
                    markersize=4,
                    label=label,
                )
                raw_plotted = True
        else:
            self.ax_raw.text(
                0.5,
                0.5,
                "No data yet",
                transform=self.ax_raw.transAxes,
                ha="center",
                va="center",
                color=axis_fg,
            )

        if raw_plotted:
            self.ax_raw.legend(loc="upper left", fontsize=8)
        self._sync_dual_axis_limits()

    def choose_log_dir(self) -> None:
        current_dir = self.lineEdit_log_dir.text().strip() or self.log_dir
        if _looks_cache_redirect(current_dir) or not self._is_valid_dir(current_dir):
            current_dir = DEFAULT_LOG_DIR
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select log directory",
            current_dir,
        )
        if not new_dir:
            return
        self.log_dir = new_dir
        self.root_log_dir = new_dir
        self.lineEdit_log_dir.setText(new_dir)

    def open_log_dir(self) -> None:
        path = self.lineEdit_log_dir.text().strip() or self.log_dir
        if not path:
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _target_dir_for_file_name(self, file_base: str) -> str:
        target_dir = self.root_log_dir
        if self.checkBox_subdir.isChecked():
            parts = file_base.split()
            if len(parts) > 1:
                folder = " ".join(parts[:-1])
                folder = re.sub(r'[<>:"/\\|?*]', "_", folder)
                target_dir = os.path.join(self.root_log_dir, folder)
        return target_dir

    def _ask_existing_file_action(self, full_path: str) -> str:
        if not os.path.exists(full_path):
            return "replace"

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("File exists")
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)
        dialog.setText(f"'{os.path.basename(full_path)}' already exists.")
        dialog.setInformativeText("Choose an action:")

        replace_btn = dialog.addButton(
            "Replace",
            QtWidgets.QMessageBox.ButtonRole.DestructiveRole,
        )
        continue_btn = dialog.addButton(
            "Continue",
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        cancel_btn = dialog.addButton(
            "Cancel",
            QtWidgets.QMessageBox.ButtonRole.RejectRole,
        )

        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is cancel_btn:
            return "cancel"
        if clicked is continue_btn:
            return "continue"
        if clicked is replace_btn:
            return "replace"
        return "cancel"

    def start_session(self) -> None:
        if self.logging_on:
            return

        file_base = self.lineEdit_log_file.text().strip()
        if not file_base:
            QtWidgets.QMessageBox.warning(self, "Missing file name", "Please provide a file name.")
            return

        root_dir = self.lineEdit_log_dir.text().strip() or self.root_log_dir
        if _looks_cache_redirect(root_dir):
            root_dir = DEFAULT_LOG_DIR
            self.lineEdit_log_dir.setText(root_dir)
        self.root_log_dir = root_dir

        target_dir = self._target_dir_for_file_name(file_base)
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Directory error",
                f"Cannot create log directory:\n{exc}",
            )
            return

        full_path = os.path.join(target_dir, f"{file_base}.txt")
        action = self._ask_existing_file_action(full_path)
        if action == "cancel":
            return

        loaded_points: list[tuple[float, float]] = []
        if action == "continue":
            loaded_points = self._load_points_from_file(full_path)

        self._close_log_file()

        try:
            self.log_file = open(full_path, "w+", encoding="utf-8", buffering=1)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "File error",
                f"Failed to open {full_path}: {exc}",
            )
            return

        self.log_path = full_path
        self.log_dir = target_dir
        self.lineEdit_log_dir.setText(target_dir)

        if action == "continue":
            self.displacements = [displacement for displacement, _load in loaded_points]
            self.loads = [load for _displacement, load in loaded_points]
        else:
            self.displacements = []
            self.loads = []

        self._load_offset_g = 0.0
        self._pending_displacement_mm = None
        if self.loads:
            self._last_logged_load = self.loads[-1]
            self._last_load_change_ts = time.monotonic()
        else:
            self._last_logged_load = None
            self._last_load_change_ts = None

        self._recalculate_derived(persist=False)
        self.logging_on = True
        self._record_name_history()
        self._set_logging_controls(True)
        self._persist_points_to_file()
        self._update_status_labels()

    def stop_session(self) -> None:
        if not self.logging_on:
            return
        self._persist_points_to_file()
        self.logging_on = False
        self._close_log_file()
        self._pending_displacement_mm = None
        self._set_logging_controls(False)
        self._update_status_labels()

    def _close_log_file(self) -> None:
        if self.log_file is not None:
            try:
                self.log_file.close()
            except Exception:
                pass
        self.log_file = None

    @staticmethod
    def _parse_float_token(token: str) -> float:
        return float(token.strip().replace(",", "."))

    def _load_points_from_file(self, full_path: str) -> list[tuple[float, float]]:
        rows: list[tuple[float, float]] = []
        try:
            with open(full_path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return rows

        payload = lines[2:] if len(lines) >= 2 else []
        for line in payload:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = [part.strip() for part in stripped.split("\t") if part.strip()]
            if len(parts) < 2:
                parts = [part.strip() for part in stripped.split() if part.strip()]
            if len(parts) < 2:
                continue
            try:
                displacement = self._parse_float_token(parts[0])
                load = self._parse_float_token(parts[1])
            except ValueError:
                continue
            rows.append((displacement, load))
        return rows

    @staticmethod
    def _format_value(value: float) -> str:
        text = f"{value:.10f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def _persist_points_to_file(self) -> None:
        if self.log_file is None:
            return

        header, units = self.header_rows()
        self.log_file.seek(0)
        self.log_file.truncate(0)
        self.log_file.write(f"{header}\n")
        self.log_file.write(f"{units}\n")

        for displacement, load, strain, stress in zip(
            self.displacements,
            self.loads,
            self.strains,
            self.stresses,
        ):
            line = "\t".join(
                (
                    self._format_value(displacement),
                    self._format_value(load),
                    self._format_value(strain),
                    self._format_value(stress),
                )
            )
            self.log_file.write(f"{line}\n")
        self.log_file.flush()

    def _reset_reference_tracking(self) -> None:
        self._strain_reference_disp = None
        self._preload_phase = True

    def _advance_reference(self, displacement_mm: float, load_g: float) -> float | None:
        self._strain_reference_disp, self._preload_phase = self.update_reference_state(
            self._strain_reference_disp,
            self._preload_phase,
            displacement_mm,
            load_g,
        )
        return self._strain_reference_disp

    def _recalculate_derived(self, *, persist: bool) -> None:
        self.strains = []
        self.stresses = []
        self._reset_reference_tracking()

        l0_mm = self._effective_l0_mm()
        diameter_mm = self._diameter_mm()

        for displacement, load in zip(self.displacements, self.loads):
            reference = self._advance_reference(displacement, load)
            strain = (
                self.strain_percent(displacement, l0_mm, reference)
                if l0_mm is not None
                else None
            )
            stress = self.stress_mpa_from_load_g(load, diameter_mm)
            self.strains.append(0.0 if strain is None else strain)
            self.stresses.append(0.0 if stress is None else stress)

        self._update_geometry_labels()
        self._update_status_labels()
        self._update_action_buttons()
        self._refresh_data_table()
        self._refresh_plot()

        if persist and self.logging_on:
            self._persist_points_to_file()

    def _handle_geometry_changed(self) -> None:
        self._recalculate_derived(persist=True)

    def _handle_displacement_enter(self) -> None:
        self._pending_displacement_mm = self._displacement_mm_from_input()
        self.spin_load_g.setFocus()
        self.spin_load_g.lineEdit().selectAll()
        self._update_status_labels()

    def _ensure_session_started(self) -> bool:
        if self.logging_on:
            return True

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Session not started")
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Information)
        dialog.setText("Start a session before adding points.")
        dialog.setInformativeText("Start a session now?")

        start_button = dialog.addButton(
            "Start session",
            QtWidgets.QMessageBox.ButtonRole.AcceptRole,
        )
        cancel_button = dialog.addButton(
            "Cancel",
            QtWidgets.QMessageBox.ButtonRole.RejectRole,
        )
        dialog.setDefaultButton(start_button)
        dialog.exec()

        if dialog.clickedButton() is not start_button:
            return False

        self.start_session()
        return self.logging_on

    def _handle_load_enter(self) -> None:
        if not self._ensure_session_started():
            return
        displacement = (
            self._pending_displacement_mm
            if self._pending_displacement_mm is not None
            else self._displacement_mm_from_input()
        )
        raw_load = float(self.spin_load_g.value())
        self._append_point(displacement, raw_load)
        self.spin_displacement.setFocus()
        self.spin_displacement.lineEdit().selectAll()

    def _append_point(self, displacement_mm: float, raw_load_g: float) -> None:
        if self.should_insert_zero_anchor_point(
            existing_point_count=len(self.displacements),
            start_points=self._current_start_points(),
            displacement_mm=displacement_mm,
        ):
            self.displacements.append(0.0)
            self.loads.append(0.0)
            self._last_logged_load = 0.0

        effective = self.effective_load_from_raw(raw_load_g, self._load_offset_g)
        if self._last_logged_load is None or abs(effective - self._last_logged_load) > ZERO_TOLERANCE_G:
            self._last_load_change_ts = time.monotonic()
        self._last_logged_load = effective
        self.displacements.append(displacement_mm)
        self.loads.append(effective)
        self._pending_displacement_mm = None
        self._recalculate_derived(persist=True)

    def add_point(self) -> None:
        if not self._ensure_session_started():
            return

        displacement = self._displacement_mm_from_input()
        raw_load = float(self.spin_load_g.value())

        self._append_point(displacement, raw_load)

        self.spin_displacement.setFocus()
        self.spin_displacement.lineEdit().selectAll()

    def handle_scale_rezero(self) -> None:
        if not self.logging_on:
            return
        anchor = self.loads[-1] if self.loads else self._effective_load_now()
        self._load_offset_g = anchor
        self.spin_load_g.setValue(0.0)
        self._update_status_labels()

    def handle_reset_displacement(self) -> None:
        self._set_displacement_input_from_mm(self._start_displacement_mm())
        self._pending_displacement_mm = None
        self._update_micrometer_display()
        self._update_status_labels()

    def undo_last_point(self) -> None:
        if not self.logging_on or not self.displacements:
            return
        self.displacements.pop()
        self.loads.pop()
        if self.loads:
            self._last_logged_load = self.loads[-1]
            self._last_load_change_ts = time.monotonic()
        else:
            self._last_logged_load = None
            self._last_load_change_ts = None
        self._pending_displacement_mm = None
        self._recalculate_derived(persist=True)

    def clear_points(self) -> None:
        if not self.logging_on or not self.displacements:
            return

        answer = QtWidgets.QMessageBox.question(
            self,
            "Clear points",
            "Remove all currently logged points from this session?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self.displacements = []
        self.loads = []
        self._last_logged_load = None
        self._last_load_change_ts = None
        self._pending_displacement_mm = None
        self._recalculate_derived(persist=True)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        self._save_settings()
        self.stop_session()
        event.accept()


def main(log_dir: str | None = None) -> QtWidgets.QWidget:
    """Launch the manual stress/strain logger and return the created window."""

    target_dir = log_dir or DEFAULT_LOG_DIR

    app = QtWidgets.QApplication.instance()
    owns_app = False
    if not isinstance(app, QtWidgets.QApplication):
        app = QtWidgets.QApplication(sys.argv)
        owns_app = True

    window = MainWindow(target_dir)
    window.showMaximized()

    WINDOWS.append(window)

    if owns_app:
        sys.exit(app.exec())
    return window


if __name__ == "__main__":
    main()
