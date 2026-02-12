from __future__ import annotations

import math
import os
import re
import sys
import time
from importlib import import_module
from pathlib import Path
from typing import TextIO

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
MM_PER_POINT = 0.001
DISPLACEMENT_MODE_MM = "mm"
DISPLACEMENT_MODE_POINTS = "points"
PLOT_VIEW_BOTH = "both"
PLOT_VIEW_RAW_ONLY = "raw_only"
UI_MAX_DECIMALS = 3
MICROMETER_DISPLAY_CYCLE = 50
MICROMETER_DISPLAY_STEP = 5
STRAIN_DIRECTION_TOLERANCE = 1e-9
LOADING_COLORS = ("#1f77b4", "#2ca02c", "#17becf", "#9467bd")
UNLOADING_COLORS = ("#d62728", "#ff7f0e", "#8c564b", "#e377c2")
HOLD_COLORS = ("#7f7f7f",)

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

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.combo_format = QtWidgets.QComboBox(self)
        self.combo_format.addItems(["Stress", "Custom"])
        layout.addWidget(self.combo_format)

        self.stacked = QtWidgets.QStackedWidget(self)
        layout.addWidget(self.stacked)

        stress = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(stress)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)

        self.s_comp = QtWidgets.QLineEdit(self)
        self.s_comp.setText("FeSiBP")
        form.addRow("Composition:", self.s_comp)

        self.s_sample = QtWidgets.QLineEdit(self)
        self.s_sample.setText("156_2")
        form.addRow("Microwire:", self.s_sample)

        self.s_number = QtWidgets.QLineEdit(self)
        self.s_number.setPlaceholderText("optional, e.g. s1")
        self.s_number.setText("s1")
        form.addRow("Sample number:", self.s_number)

        self.s_current = QtWidgets.QLineEdit(self)
        self.s_current.setText("74mA")
        form.addRow("Current:", self.s_current)

        self.s_notes = QtWidgets.QLineEdit(self)
        self.s_notes.setPlaceholderText("optional, e.g. no glass")
        form.addRow("Notes:", self.s_notes)

        self.stacked.addWidget(stress)
        self.stacked.addWidget(QtWidgets.QWidget(self))  # custom mode placeholder

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.reset_btn = QtWidgets.QPushButton("Reset", self)
        buttons.addWidget(self.reset_btn)
        layout.addLayout(buttons)

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

        wire = self.s_sample.text().strip()
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
        zero_display: int,
    ) -> int:
        return cls._snap_to_micrometer_step(float(zero_display) + float(displacement_points))

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
        log_grid.addWidget(self.name_builder, 4, 0, 1, 4)

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
        self.combo_diameter_unit = QtWidgets.QComboBox()
        self.combo_diameter_unit.addItems(["um", "mm"])
        diameter_layout.addWidget(self.spin_diameter, stretch=1)
        diameter_layout.addWidget(self.combo_diameter_unit)
        geom_form.addRow("Diameter:", diameter_row)

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
        self.combo_displacement_mode.addItem("Micrometer points (10^-3 mm)", DISPLACEMENT_MODE_POINTS)
        input_form.addRow("Displacement mode:", self.combo_displacement_mode)

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

        self.label_micrometer_zero = QtWidgets.QLabel("Micrometer at d=0:")
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

        self.label_idle_timer = QtWidgets.QLabel("Idle: waiting", load_row)
        self.label_idle_timer.setWordWrap(False)
        self.label_idle_timer.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter
        )
        self.label_idle_timer.setMinimumWidth(210)
        self.label_idle_timer.setStyleSheet(
            "font-weight: 700; padding: 3px 8px; border-radius: 4px; "
            "background-color: #5a5a5a; color: #ffffff;"
        )
        load_layout.addWidget(self.label_idle_timer, stretch=0)
        input_form.addRow("Load:", load_row)

        scale_row = QtWidgets.QHBoxLayout()
        self.pushButton_reset_displacement = QtWidgets.QPushButton("Reset d=0")
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
        self._plot_derived_visible = True

        if FigureCanvas is not None:
            self.figure = Figure(figsize=(10, 5), tight_layout=True)
            canvas = FigureCanvas(self.figure)
            self.canvas = canvas
            if NavigationToolbar is not None:
                toolbar = NavigationToolbar(canvas, self)
                self._plot_toolbar = toolbar
                plot_layout.addWidget(toolbar)
            plot_layout.addWidget(canvas)
            self._rebuild_plot_axes(show_derived=True)
        else:
            placeholder = QtWidgets.QLabel("Matplotlib Qt backend is unavailable.")
            placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            plot_layout.addWidget(placeholder, stretch=1)

        right_layout.addWidget(self.plot_frame, stretch=5)

        self.group_table = QtWidgets.QGroupBox("Logged Data", right_panel)
        table_layout = QtWidgets.QVBoxLayout(self.group_table)
        table_layout.setContentsMargins(8, 8, 8, 8)
        table_layout.setSpacing(6)
        self.table_data = QtWidgets.QTableWidget(0, 4, self.group_table)
        self.table_data.setHorizontalHeaderLabels(
            ["Displacement (mm)", "Load (g)", "Strain (%)", "Stress (MPa)"]
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
            3, QtWidgets.QHeaderView.ResizeMode.Stretch
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

        self.lineEdit_log_file.returnPressed.connect(self.start_session)
        self.spin_displacement.lineEdit().returnPressed.connect(self._handle_displacement_enter)
        self.spin_load_g.lineEdit().returnPressed.connect(self._handle_load_enter)

        self.spin_l0_mm.valueChanged.connect(self._handle_geometry_changed)
        self.spin_diameter.valueChanged.connect(self._handle_geometry_changed)
        self.combo_diameter_unit.currentIndexChanged.connect(self._handle_geometry_changed)
        self.combo_displacement_mode.currentIndexChanged.connect(
            self._handle_displacement_mode_changed
        )
        self.combo_plot_view.currentIndexChanged.connect(self._handle_plot_view_changed)
        self.spin_displacement.valueChanged.connect(self._update_micrometer_display)
        self.spin_micrometer_zero.valueChanged.connect(self._update_micrometer_display)
        self.spin_load_g.valueChanged.connect(self._update_status_labels)

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

        stored_mode = self.settings.value("displacement_mode", DISPLACEMENT_MODE_MM, type=str)
        self.combo_displacement_mode.blockSignals(True)
        self._select_displacement_mode(stored_mode)
        self.combo_displacement_mode.blockSignals(False)

        self._apply_displacement_mode(self._current_displacement_mode(), preserve_mm=False)
        disp_mm = float(self.settings.value("input_disp_mm", 0.0))
        self._set_displacement_input_from_mm(disp_mm)

        self.spin_load_g.setValue(float(self.settings.value("input_load_raw", 0.0)))
        self._load_offset_g = float(self.settings.value("load_offset_g", 0.0) or 0.0)
        zero_display = int(self.settings.value("micrometer_zero_display", 0) or 0)
        self.spin_micrometer_zero.setValue(self._snap_to_micrometer_step(zero_display))

        stored_plot_view = self.settings.value("plot_view", PLOT_VIEW_BOTH, type=str)
        self.combo_plot_view.blockSignals(True)
        self._select_plot_view(stored_plot_view)
        self.combo_plot_view.blockSignals(False)
        self._rebuild_plot_axes(show_derived=self._plot_view_shows_derived())

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
        self.settings.setValue("displacement_mode", self._current_displacement_mode())
        self.settings.setValue("input_disp_mm", self._displacement_mm_from_input())
        self.settings.setValue("input_load_raw", self.spin_load_g.value())
        self.settings.setValue("load_offset_g", self._load_offset_g)
        self.settings.setValue("micrometer_zero_display", self.spin_micrometer_zero.value())
        self.settings.setValue("plot_view", self._current_plot_view())

    def _current_displacement_mode(self) -> str:
        mode = self.combo_displacement_mode.currentData()
        if isinstance(mode, str):
            return mode
        return DISPLACEMENT_MODE_MM

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
        return self._current_plot_view() == PLOT_VIEW_BOTH

    def _apply_displacement_mode(self, mode: str, *, preserve_mm: bool = True) -> None:
        current_mm = self._displacement_mm_from_input() if preserve_mm else 0.0

        if mode == DISPLACEMENT_MODE_POINTS:
            self.spin_displacement.setDecimals(0)
            self.spin_displacement.setSingleStep(10.0)
            self.spin_displacement.setValue(0.0)
            self.spin_displacement.setSuffix(" x10^-3 mm")
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
            return values, "Displacement (10^-3 mm)"
        return list(self.displacements), "Displacement (mm)"

    def _handle_displacement_mode_changed(self, _index: int) -> None:
        self._apply_displacement_mode(self._current_displacement_mode(), preserve_mm=True)
        self._pending_displacement_mm = None
        self._update_status_labels()
        self._refresh_plot()

    def _handle_plot_view_changed(self, _index: int) -> None:
        self._rebuild_plot_axes(show_derived=self._plot_view_shows_derived())
        self._refresh_plot()

    def _rebuild_plot_axes(self, *, show_derived: bool) -> None:
        if self.figure is None:
            return
        self.figure.clear()
        self.ax_raw = self.figure.add_subplot(111 if not show_derived else 121)
        self.ax_derived = self.figure.add_subplot(122) if show_derived else None
        self._plot_derived_visible = show_derived

    def _update_micrometer_display(self) -> None:
        if not hasattr(self, "line_micrometer_display"):
            return
        if self._current_displacement_mode() != DISPLACEMENT_MODE_POINTS:
            self.line_micrometer_display.clear()
            return

        points_value = float(self.spin_displacement.value())
        zero_display = int(self.spin_micrometer_zero.value())
        displayed = self.micrometer_display_from_points(points_value, zero_display)
        self.line_micrometer_display.setText(str(displayed))

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
            self.label_cross_section.setText(f"{self._format_ui(area_mm2)} mm^2")

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

    def _set_idle_indicator(self, text: str, fg: str, bg: str) -> None:
        self.label_idle_timer.setText(text)
        self.label_idle_timer.setStyleSheet(
            "font-weight: 700; padding: 3px 8px; border-radius: 4px; "
            f"background-color: {bg}; color: {fg};"
        )

    def _update_idle_timer_label(self) -> None:
        if not hasattr(self, "label_idle_timer"):
            return
        if not self.logging_on:
            self._set_idle_indicator("Idle: stopped", "#f0f0f0", "#5a5a5a")
            return
        if self._last_load_change_ts is None:
            self._set_idle_indicator("Idle: waiting", "#1b1b1b", "#ffd166")
            return

        elapsed = max(0, int(time.monotonic() - self._last_load_change_ts))
        remaining = max(0, 60 - elapsed)
        text = f"Idle: {elapsed}s ({remaining}s left)"
        if remaining <= 5:
            self._set_idle_indicator(text, "#ffffff", "#d7263d")
        elif remaining <= 10:
            self._set_idle_indicator(text, "#1b1b1b", "#ffb347")
        else:
            self._set_idle_indicator(text, "#ffffff", "#228b22")

    def _refresh_data_table(self) -> None:
        if not hasattr(self, "table_data"):
            return
        self.table_data.setRowCount(len(self.displacements))
        for row, (displacement, load, strain, stress) in enumerate(
            zip(self.displacements, self.loads, self.strains, self.stresses)
        ):
            values = (
                self._format_value(displacement),
                self._format_value(load),
                self._format_value(strain),
                self._format_value(stress),
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
        show_derived = self._plot_view_shows_derived()
        if self.ax_raw is None or show_derived != self._plot_derived_visible:
            self._rebuild_plot_axes(show_derived=show_derived)
        if self.ax_raw is None:
            return

        axis_bg, axis_fg, _ = self._apply_plot_theme()
        self.ax_raw.clear()
        if show_derived and self.ax_derived is not None:
            self.ax_derived.clear()

        axes = [self.ax_raw]
        if show_derived and self.ax_derived is not None:
            axes.append(self.ax_derived)

        for axis in axes:
            axis.set_facecolor(axis_bg)
            axis.tick_params(colors=axis_fg)
            for spine in axis.spines.values():
                spine.set_color(axis_fg)
            axis.grid(True, color=(0.35, 0.35, 0.35, 0.35))

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

        self.figure.tight_layout()
        self.canvas.draw_idle()

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

        l0_mm = float(self.spin_l0_mm.value())
        diameter_mm = self._diameter_mm()

        for displacement, load in zip(self.displacements, self.loads):
            reference = self._advance_reference(displacement, load)
            strain = self.strain_percent(displacement, l0_mm, reference)
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
        self.spin_displacement.setValue(0.0)
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
