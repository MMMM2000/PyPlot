"""AC susceptibility logger built on the current annealing workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
import os
import re
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Callable, Sequence, cast

from PyQt6 import QtCore, QtGui, QtWidgets
try:
    import pyqtgraph as pg
except Exception:  # pragma: no cover - optional runtime fallback
    pg = None  # type: ignore[assignment]

from data_logging.current_annealing_logger.current_annealing_logger import (
    DEFAULT_LOG_DIR,
    MainWindow as CurrentAnnealingWindow,
    SUPPLY_PROFILES,
    _apply_app_font_to_matplotlib,
)
from plotting.shared.utils import ensure_app_theme

from .lcr6000 import (
    DEFAULT_BAUDRATE,
    Lcr6000Reading,
    Lcr6000Serial,
    Lcr6000Settings,
    LCR_FRONT_PANEL_VOLTAGE_PRESETS_V as _LCR_FRONT_PANEL_VOLTAGE_PRESETS_V,
    SUPPORTED_FUNCTIONS,
    SUPPORTED_MONITORS,
    available_serial_ports,
    build_settings_plan,
    commands_for_settings,
    parse_numeric_list,
)
from . import sweep


HEADER_LINE = (
    "# Current (mA)\tVoltage (V)\tResistance (Ohm)\t"
    "AC plan index\tLCR frequency (Hz)\tLCR level mode\tLCR level\t"
    "LCR function\tLCR primary\tLCR secondary\tLCR monitor1\t"
    "LCR monitor2\tLCR comparator\tLCR raw"
)

BASELINE_HEADER_LINE = (
    "# Timestamp UTC\tBaseline setting index\tBaseline repeat index\t"
    "LCR frequency (Hz)\tLCR level mode\tLCR level\tLCR function\t"
    "LCR primary\tLCR secondary\tLCR monitor1\tLCR monitor2\t"
    "LCR comparator\tLCR raw"
)

PRACTICAL_FREQUENCY_PRESETS_HZ = [
    10.0,
    20.0,
    50.0,
    100.0,
    200.0,
    500.0,
    1000.0,
    2000.0,
    5000.0,
    10000.0,
    20000.0,
    50000.0,
    100000.0,
    200000.0,
]

DEFAULT_FREQUENCY_PRESETS_HZ = list(PRACTICAL_FREQUENCY_PRESETS_HZ)
LCR_FRONT_PANEL_VOLTAGE_PRESETS_V = list(_LCR_FRONT_PANEL_VOLTAGE_PRESETS_V)
OWON_DEFAULT_VOLTAGE_LIMIT_V = 61.0
HMP_DEFAULT_VOLTAGE_LIMIT_V = 30.0
AC_DEFAULT_LOG_DIR = Path.home() / "Downloads" / "ac_susceptibility"
AC_DEFAULT_SWEEP_BASE = "ac_susc_current_sweep"
AC_LEGACY_INHERITED_BASES = {"anneal_log", "ac_susceptibility_log"}
AC_PLOT_REFRESH_INTERVAL_S = 1.0
AC_PLOT_RECENT_POINTS = 3000
AC_PLOT_MAX_POINTS_PER_CONDITION = 160
AC_PLOT_JITTER_PX = 5.0
AC_PLOT_SPREAD_PIXELS = {
    "off": 0.0,
    "small": 5.0,
    "medium": 9.0,
    "large": 14.0,
}
AC_UI_TELEMETRY_INTERVAL_MS = 16
AC_UI_TELEMETRY_REPORT_TICKS = 60
AC_DIAGNOSTICS_DEFAULT_PATH = AC_DEFAULT_LOG_DIR / "ac_susc_diagnostics.jsonl"
AC_LCR_SLOW_RETRY_MIN_FREQUENCY_HZ = 1000.0
AC_LCR_SLOW_RETRY_MIN_RATE_HZ = 20.0
AC_LCR_SLOW_RETRY_CHECK_S = 3.0
AC_LCR_SLOW_RETRY_DISCARD_S = 3.0
AC_LCR_SLOW_RETRY_MAX_ATTEMPTS = 2
LEGACY_DEFAULT_FREQUENCY_TEXTS = {
    "10, 20, 100, 1000, 2000, 10000, 100000, 200000",
    "100, 1k, 10k, 100k",
    "100, 1000, 10000, 100000",
}
LEGACY_DEFAULT_LEVEL_TEXTS = {"0.1, 0.3, 1.0", "0.1, 0.3, 1"}


class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """Double spin box that keeps fractional input without fixed trailing zeros."""

    def textFromValue(self, value: float) -> str:  # type: ignore[override]
        return f"{float(value):.6g}"

    def valueFromText(self, text: str) -> float:  # type: ignore[override]
        cleaned = text
        suffix = self.suffix()
        if suffix and cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
        cleaned = cleaned.strip().replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return float(self.value())


@dataclass
class AcPlotPoint:
    elapsed_s: float
    model: str
    frequency_hz: float
    amplitude_v: float
    current_mA: float
    ls_h: float | None
    rs_ohm: float | None
    current_actual_mA: float | None = None
    wire_resistance_ohm: float | None = None
    psu_power_w: float | None = None


@dataclass
class AcPlotChannel:
    key: str
    label: str
    color: str
    getter: Callable[[AcPlotPoint], float | None]


@dataclass
class AcPlotTileWidgets:
    visible: QtWidgets.QCheckBox
    x_combo: QtWidgets.QComboBox
    y_left_combo: QtWidgets.QComboBox
    y_right_combo: QtWidgets.QComboBox
    y_extra_combo: QtWidgets.QComboBox


@dataclass
class AcPyQtGraphTile:
    widget: Any
    plot_item: Any
    left_item: Any
    right_view: Any
    right_item: Any
    extra_axis: Any
    extra_view: Any
    extra_item: Any
    no_data_item: Any


class AcPlotConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure AC plots")
        self.setModal(False)
        self.resize(880, 320)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.body_layout = QtWidgets.QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.body_layout, stretch=1)
        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        close_button = QtWidgets.QPushButton("Close", self)
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)


def _fetch_lcr_reading_from_meter(meter: Any, *, attempts: int = 3) -> Lcr6000Reading:
    last_empty = False
    for attempt in range(1, max(1, int(attempts)) + 1):
        reading = meter.fetch_impedance()
        if reading.raw.strip():
            return reading
        last_empty = True
        if attempt < attempts:
            time.sleep(0.25)
    if last_empty:
        raise RuntimeError("LCR returned an empty response during baseline measurement")
    raise RuntimeError("LCR did not return a measurement")


def _should_retry_slow_lcr_setting(setting: Lcr6000Settings) -> bool:
    if setting.aperture.strip().upper() != "FAST":
        return False
    return float(setting.frequency_hz) >= AC_LCR_SLOW_RETRY_MIN_FREQUENCY_HZ


class _SlowBaselineCadenceError(RuntimeError):
    def __init__(self, *, rate_hz: float, reads: int) -> None:
        super().__init__(f"{rate_hz:.3g} Hz from {reads} reads")
        self.rate_hz = rate_hz
        self.reads = reads


def _plot_point_from_lcr_reading(
    setting: Lcr6000Settings,
    reading: Lcr6000Reading,
    *,
    elapsed_s: float,
    current_mA: float = 0.0,
) -> AcPlotPoint:
    ls_h, rs_ohm = MainWindow._lcr_ls_rs_values(setting, reading)
    return AcPlotPoint(
        elapsed_s=float(elapsed_s),
        model=setting.function,
        frequency_hz=float(setting.frequency_hz),
        amplitude_v=float(setting.level_value if setting.level_mode == "voltage" else math.nan),
        current_mA=float(current_mA),
        ls_h=ls_h,
        rs_ohm=rs_ohm,
    )


class AcBaselineWorker(QtCore.QObject):
    task_changed = QtCore.pyqtSignal(str)
    progress_changed = QtCore.pyqtSignal(float, float)
    plot_point_ready = QtCore.pyqtSignal(object)
    finished = QtCore.pyqtSignal(str, bool)
    failed = QtCore.pyqtSignal(str)

    def __init__(
        self,
        *,
        meter: Any,
        plan: Sequence[Lcr6000Settings],
        output_path: Path,
        point_duration_s: float,
        settle_s: float,
        total_planned_s: float,
    ) -> None:
        super().__init__()
        self.meter = meter
        self.plan = list(plan)
        self.output_path = Path(output_path)
        self.point_duration_s = max(0.0, float(point_duration_s))
        self.settle_s = max(0.0, float(settle_s))
        self.total_planned_s = max(0.001, float(total_planned_s))
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def _sleep_with_stop(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if self._stop_requested:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return not self._stop_requested

    @QtCore.pyqtSlot()
    def run(self) -> None:
        started = time.monotonic()
        stopped = False
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("w", encoding="utf-8", newline="") as fh:
                MainWindow._write_baseline_header(
                    fh,
                    self.plan,
                    point_duration_s=self.point_duration_s,
                    settle_s=self.settle_s,
                )
                for setting_index, setting in enumerate(self.plan, start=1):
                    if self._stop_requested:
                        stopped = True
                        break
                    self.meter.configure(setting)
                    attempt = 1
                    if self.settle_s and not self._sleep_with_stop(self.settle_s):
                        stopped = True
                        break
                    while True:
                        try:
                            stopped = self._measure_baseline_setting(
                                fh=fh,
                                started=started,
                                setting_index=setting_index,
                                setting=setting,
                                attempt=attempt,
                                check_cadence=True,
                            )
                            break
                        except _SlowBaselineCadenceError as exc:
                            if self._stop_requested:
                                stopped = True
                                break
                            if attempt > AC_LCR_SLOW_RETRY_MAX_ATTEMPTS:
                                self._write_baseline_warning(
                                    fh,
                                    setting_index=setting_index,
                                    setting=setting,
                                    message=f"slow LCR cadence persisted after {attempt} attempts: {exc}",
                                )
                                stopped = self._measure_baseline_setting(
                                    fh=fh,
                                    started=started,
                                    setting_index=setting_index,
                                    setting=setting,
                                    attempt=attempt + 1,
                                    check_cadence=False,
                                )
                                break
                            self._write_baseline_warning(
                                fh,
                                setting_index=setting_index,
                                setting=setting,
                                message=f"slow LCR cadence attempt {attempt}: {exc}; reconfiguring and retrying",
                            )
                            self.task_changed.emit(
                                "Current task: empty-coil baseline - "
                                f"{setting.function}, {setting.frequency_hz:g} Hz, "
                                f"{setting.level_value:g} {setting.level_mode}, retrying slow LCR cadence"
                            )
                            self.meter.configure(setting)
                            if self.settle_s and not self._sleep_with_stop(self.settle_s):
                                stopped = True
                                break
                            if not self._discard_lcr_reads():
                                stopped = True
                                break
                            attempt += 1
                    if stopped:
                        break
            self.finished.emit(str(self.output_path), stopped)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _measure_baseline_setting(
        self,
        *,
        fh: Any,
        started: float,
        setting_index: int,
        setting: Lcr6000Settings,
        attempt: int,
        check_cadence: bool,
    ) -> bool:
        point_started = time.monotonic()
        first_read_monotonic: float | None = None
        cadence_checked = False
        repeat_index = 0
        while True:
            if self._stop_requested:
                return True
            repeat_index += 1
            elapsed_point = time.monotonic() - point_started
            suffix = f", retry {attempt}" if attempt > 1 else ""
            self.task_changed.emit(
                "Current task: empty-coil baseline - "
                f"{setting.function}, {setting.frequency_hz:g} Hz, "
                f"{setting.level_value:g} {setting.level_mode}, "
                f"{MainWindow._format_duration(elapsed_point)} / {MainWindow._format_duration(self.point_duration_s)}"
                f"{suffix}"
            )
            reading = _fetch_lcr_reading_from_meter(self.meter)
            read_monotonic = time.monotonic()
            if first_read_monotonic is None:
                first_read_monotonic = read_monotonic
            row = MainWindow._format_baseline_row(
                setting_index=setting_index,
                repeat_index=repeat_index,
                setting=setting,
                reading=reading,
            )
            MainWindow._write_baseline_row(fh, row)
            elapsed_total = read_monotonic - started
            self.plot_point_ready.emit(
                _plot_point_from_lcr_reading(setting, reading, elapsed_s=elapsed_total)
            )
            self.progress_changed.emit(elapsed_total, self.total_planned_s)
            if check_cadence and _should_retry_slow_lcr_setting(setting) and not cadence_checked:
                check_elapsed = read_monotonic - point_started
                if check_elapsed >= AC_LCR_SLOW_RETRY_CHECK_S:
                    cadence_checked = True
                    active_elapsed = max(1e-9, read_monotonic - first_read_monotonic)
                    completed_intervals = max(1, repeat_index - 1)
                    rate_hz = completed_intervals / active_elapsed
                    if rate_hz < AC_LCR_SLOW_RETRY_MIN_RATE_HZ:
                        raise _SlowBaselineCadenceError(rate_hz=rate_hz, reads=repeat_index)
            if self.point_duration_s > 0.0:
                if read_monotonic - point_started >= self.point_duration_s:
                    break
            else:
                break
        return False

    def _discard_lcr_reads(self) -> bool:
        deadline = time.monotonic() + max(0.0, AC_LCR_SLOW_RETRY_DISCARD_S)
        while time.monotonic() < deadline:
            if self._stop_requested:
                return False
            _fetch_lcr_reading_from_meter(self.meter)
        return not self._stop_requested

    def _write_baseline_warning(
        self,
        fh: Any,
        *,
        setting_index: int,
        setting: Lcr6000Settings,
        message: str,
    ) -> None:
        fh.write(
            "# WARN "
            f"setting_index={setting_index} "
            f"function={setting.function} "
            f"frequency_hz={setting.frequency_hz:g} "
            f"{setting.level_mode}={setting.level_value:g} "
            f"{message}\n"
        )
        fh.flush()


class AcSweepWorker(QtCore.QObject):
    row_ready = QtCore.pyqtSignal(object)
    finished = QtCore.pyqtSignal(str, bool)
    failed = QtCore.pyqtSignal(str)

    def __init__(
        self,
        *,
        config: sweep.AcSweepConfig,
        lcr: Any,
        psu: sweep.CurrentSource,
        output_path: Path,
    ) -> None:
        super().__init__()
        self.config = config
        self.lcr = lcr
        self.psu = psu
        self.output_path = Path(output_path)
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def _sleep_with_stop(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if self._stop_requested:
                return
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            sweep.run_ac_sweep(
                config=self.config,
                lcr=self.lcr,
                psu=self.psu,
                output_path=self.output_path,
                progress=self.row_ready.emit,
                stop_requested=lambda: self._stop_requested,
                sleep=self._sleep_with_stop,
            )
            self.finished.emit(str(self.output_path), False)
        except Exception as exc:
            if self._stop_requested:
                self.finished.emit(str(self.output_path), True)
                return
            self.failed.emit(str(exc))


class MainWindow(CurrentAnnealingWindow):
    """Current annealing logger extended with LCR-6200 measurements."""

    def __init__(self) -> None:
        self.ac_settings = QtCore.QSettings("microwire", "ac_susceptibility_logger")
        self.lcr_meter: Lcr6000Serial | None = None
        self._lcr_plan: list[Lcr6000Settings] = []
        self._lcr_plan_index = 0
        self._lcr_last_reading: Lcr6000Reading | None = None
        self._lcr_last_error = ""
        self._ac_sweep_running = False
        self._ac_sweep_stop_requested = False
        self._ac_progress_total = 0
        self._ac_progress_value = 0
        self._ac_progress_started_monotonic = 0.0
        self._ac_last_plot_refresh_monotonic = 0.0
        self._ac_last_plot_refresh_duration_s = 0.0
        self._ac_plot_dirty = False
        self._ac_plot_refresh_timer: QtCore.QTimer | None = None
        self._ac_last_task_text = "Current task: idle"
        self._ac_diagnostics_enabled = False
        self._ac_diagnostics_path = AC_DIAGNOSTICS_DEFAULT_PATH
        self._ac_ui_telemetry_timer: QtCore.QTimer | None = None
        self._ac_ui_telemetry_last_s = 0.0
        self._ac_ui_telemetry_ticks = 0
        self._ac_ui_telemetry_sum_s = 0.0
        self._ac_ui_telemetry_max_s = 0.0
        self._ac_lcr_scroll_area: QtWidgets.QScrollArea | None = None
        self._auto_detect_used_connected_psu = False
        self._ac_plot_points: list[AcPlotPoint] = []
        self._plot_tiles: list[AcPlotTileWidgets] = []
        self._ac_pg_tiles: list[AcPyQtGraphTile] = []
        self._ac_plot_render_state: list[dict[str, Any]] = []
        self.plot_config_dialog: AcPlotConfigDialog | None = None
        self._ac_output_settings_ready = False
        self._ac_loading_settings = False
        self._ac_psu_backend = "hmp4030"
        self._ac_psu_resource = ""
        self._ac_psu_baudrate = 115200
        self._ac_progress_units = "count"
        self._ac_active_sweep_config: sweep.AcSweepConfig | None = None
        self._ac_worker_thread: QtCore.QThread | None = None
        self._ac_worker: QtCore.QObject | None = None
        super().__init__()
        self.setWindowTitle("AC Susceptibility Logger")
        self._restore_ac_developer_settings()
        self._install_ac_developer_menu()
        self._load_ac_output_settings()
        self._install_lcr_controls()
        self._detach_inherited_psu_settings()
        self._simplify_inherited_ac_workflow()
        self._install_ac_sticky_progress()
        self._load_lcr_settings()
        self.populate_lcr_ports()
        self.populate_ac_psu_ports()
        self._update_ac_sweep_estimate()
        self._set_default_log_name()
        self._start_ac_plot_refresh_timer()
        if self._ac_diagnostics_enabled:
            self._start_ac_ui_telemetry_timer()

    def init_graph_window(self):  # type: ignore[override]
        container = getattr(getattr(self, "ui", None), "plot_container", None)
        if pg is None or not isinstance(container, QtWidgets.QWidget):
            super().init_graph_window()
            if hasattr(self, "fig"):
                self.figure = self.fig
            container = getattr(getattr(self, "ui", None), "plot_container", None)
            layout = container.layout() if isinstance(container, QtWidgets.QWidget) else None
            if layout is not None:
                self._install_ac_plot_dashboard(container, layout)
            self._refresh_ac_plots(force=True)
            return
        self.fig = None
        self.figure = None
        self.canvas = None
        self.toolbar = None
        layout = container.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._install_ac_plot_dashboard(container, layout)
        self._refresh_ac_plots(force=True)

    def _install_ac_plot_dashboard(self, container: QtWidgets.QWidget, layout: QtWidgets.QLayout) -> None:
        header = QtWidgets.QFrame(container)
        header.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(12)
        title = QtWidgets.QLabel("AC susceptibility dashboard", header)
        font = title.font()
        font.setPointSize(max(font.pointSize(), 12))
        font.setBold(True)
        title.setFont(font)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        self.button_plot_setup = QtWidgets.QPushButton("Configure plots", header)
        self.button_plot_setup.clicked.connect(self._show_plot_config_dialog)
        header_layout.addWidget(self.button_plot_setup)
        layout.insertWidget(0, header)

        self.plot_config_dialog = AcPlotConfigDialog(self)
        config_box = QtWidgets.QGroupBox("Plot Dashboard", self.plot_config_dialog)
        config_layout = QtWidgets.QGridLayout(config_box)
        config_layout.setContentsMargins(8, 8, 8, 8)
        config_layout.setHorizontalSpacing(10)
        config_layout.setVerticalSpacing(6)
        preset_row = QtWidgets.QHBoxLayout()
        current_preset = QtWidgets.QPushButton("Current preset", config_box)
        current_preset.clicked.connect(lambda: self._apply_plot_preset("current"))
        preset_row.addWidget(current_preset)
        frequency_preset = QtWidgets.QPushButton("Frequency preset", config_box)
        frequency_preset.clicked.connect(lambda: self._apply_plot_preset("frequency"))
        preset_row.addWidget(frequency_preset)
        preset_row.addStretch(1)
        config_layout.addWidget(QtWidgets.QLabel("Presets", config_box), 0, 0)
        config_layout.addLayout(preset_row, 0, 1, 1, 4)

        self.comboBox_ac_plot_spread = QtWidgets.QComboBox(config_box)
        self.comboBox_ac_plot_spread.addItem("Off", "off")
        self.comboBox_ac_plot_spread.addItem("Small", "small")
        self.comboBox_ac_plot_spread.addItem("Medium", "medium")
        self.comboBox_ac_plot_spread.addItem("Large", "large")
        self._set_combo_data(
            self.comboBox_ac_plot_spread,
            self.ac_settings.value("plot_x_spread", "small", type=str),
        )
        self.comboBox_ac_plot_spread.currentIndexChanged.connect(
            lambda *_args: self._handle_plot_config_changed()
        )
        config_layout.addWidget(QtWidgets.QLabel("Repeated-X spread", config_box), 1, 0)
        config_layout.addWidget(self.comboBox_ac_plot_spread, 1, 1, 1, 2)

        for column, label in enumerate(("Tile", "Show", "Bottom X", "Left Y", "Right Y", "Far-right Y")):
            config_layout.addWidget(QtWidgets.QLabel(label, config_box), 2, column)

        self._plot_tiles = []
        defaults = [
            (True, "elapsed_s", "rs_ohm", "ls_h", ""),
            (True, "current_actual_mA", "rs_ohm", "ls_h", "wire_resistance_ohm"),
            (True, "frequency_hz", "rs_ohm", "ls_h", ""),
            (True, "amplitude_v", "rs_ohm", "ls_h", ""),
        ]
        for tile_index, (visible_default, x_default, y_default, right_default, extra_default) in enumerate(defaults):
            visible = QtWidgets.QCheckBox(config_box)
            x_combo = QtWidgets.QComboBox(config_box)
            y_left_combo = QtWidgets.QComboBox(config_box)
            y_right_combo = QtWidgets.QComboBox(config_box)
            y_extra_combo = QtWidgets.QComboBox(config_box)
            for combo in (x_combo, y_left_combo):
                for channel in self._plot_channels():
                    combo.addItem(channel.label, channel.key)
            for combo in (y_right_combo, y_extra_combo):
                combo.addItem("(none)", "")
                for channel in self._plot_channels():
                    combo.addItem(channel.label, channel.key)
            prefix = f"plot_tile_{tile_index}"
            visible.setChecked(bool(self.ac_settings.value(f"{prefix}_visible", visible_default, type=bool)))
            self._set_combo_data(x_combo, self.ac_settings.value(f"{prefix}_x", x_default, type=str))
            self._set_combo_data(y_left_combo, self.ac_settings.value(f"{prefix}_y_left", y_default, type=str))
            self._set_combo_data(y_right_combo, self.ac_settings.value(f"{prefix}_y_right", right_default, type=str))
            self._set_combo_data(y_extra_combo, self.ac_settings.value(f"{prefix}_y_extra", extra_default, type=str))
            for widget in (visible, x_combo, y_left_combo, y_right_combo, y_extra_combo):
                signal = widget.toggled if isinstance(widget, QtWidgets.QCheckBox) else widget.currentIndexChanged
                signal.connect(lambda *_args: self._handle_plot_config_changed())
            row = tile_index + 3
            config_layout.addWidget(QtWidgets.QLabel(f"Plot {tile_index + 1}", config_box), row, 0)
            config_layout.addWidget(visible, row, 1)
            config_layout.addWidget(x_combo, row, 2)
            config_layout.addWidget(y_left_combo, row, 3)
            config_layout.addWidget(y_right_combo, row, 4)
            config_layout.addWidget(y_extra_combo, row, 5)
            self._plot_tiles.append(
                AcPlotTileWidgets(
                    visible=visible,
                    x_combo=x_combo,
                    y_left_combo=y_left_combo,
                    y_right_combo=y_right_combo,
                    y_extra_combo=y_extra_combo,
                )
            )
        self.plot_config_dialog.body_layout.addWidget(config_box)
        if pg is not None:
            self._install_ac_pyqtgraph_tiles(container, layout)

    def _install_ac_pyqtgraph_tiles(self, container: QtWidgets.QWidget, layout: QtWidgets.QLayout) -> None:
        grid_widget = QtWidgets.QWidget(container)
        grid_layout = QtWidgets.QGridLayout(grid_widget)
        grid_layout.setContentsMargins(10, 10, 10, 10)
        grid_layout.setHorizontalSpacing(18)
        grid_layout.setVerticalSpacing(18)
        self._ac_pg_tiles = []
        for index in range(4):
            plot_widget = pg.PlotWidget(grid_widget)
            plot_widget.setMinimumSize(320, 230)
            plot_widget.setBackground(self._qt_color_to_tuple(self.palette().color(QtGui.QPalette.ColorRole.Base)))
            plot_item = plot_widget.getPlotItem()
            plot_item.showGrid(x=True, y=True, alpha=0.22)
            plot_item.showAxis("right")
            plot_item.getAxis("top").setStyle(showValues=False)
            plot_item.getAxis("right").setStyle(showValues=True)
            left_item = pg.PlotDataItem(pen=None, symbol="o", symbolSize=4)
            right_item = pg.PlotDataItem(pen=None, symbol="s", symbolSize=4)
            extra_item = pg.PlotDataItem(pen=pg.mkPen("#f59e0b", width=1.25), symbol="o", symbolSize=4)
            plot_item.addItem(left_item)
            right_view = pg.ViewBox()
            plot_item.scene().addItem(right_view)
            plot_item.getAxis("right").linkToView(right_view)
            right_view.setXLink(plot_item.vb)
            right_view.addItem(right_item)
            extra_axis = pg.AxisItem("right")
            plot_item.layout.addItem(extra_axis, 2, 3)
            extra_view = pg.ViewBox()
            plot_item.scene().addItem(extra_view)
            extra_axis.linkToView(extra_view)
            extra_view.setXLink(plot_item.vb)
            extra_view.addItem(extra_item)
            no_data_item = pg.TextItem("No AC data yet", color=self._plot_theme_text_qcolor(), anchor=(0.5, 0.5))
            plot_item.addItem(no_data_item)
            no_data_item.setPos(0.5, 0.5)
            row, column = divmod(index, 2)
            grid_layout.addWidget(plot_widget, row, column)
            runtime = AcPyQtGraphTile(
                widget=plot_widget,
                plot_item=plot_item,
                left_item=left_item,
                right_view=right_view,
                right_item=right_item,
                extra_axis=extra_axis,
                extra_view=extra_view,
                extra_item=extra_item,
                no_data_item=no_data_item,
            )
            self._ac_pg_tiles.append(runtime)
            plot_item.vb.sigResized.connect(
                lambda *_args, _runtime=runtime: self._update_pg_linked_views(_runtime)
            )
            self._update_pg_linked_views(runtime)
        layout.addWidget(grid_widget, 1)
        self._ac_plot_render_state = [{} for _ in self._ac_pg_tiles]

    @staticmethod
    def _update_pg_linked_views(runtime: AcPyQtGraphTile) -> None:
        geometry = runtime.plot_item.vb.sceneBoundingRect()
        runtime.right_view.setGeometry(geometry)
        runtime.right_view.linkedViewChanged(runtime.plot_item.vb, runtime.right_view.XAxis)
        runtime.extra_view.setGeometry(geometry)
        runtime.extra_view.linkedViewChanged(runtime.plot_item.vb, runtime.extra_view.XAxis)

    @staticmethod
    def _qt_color_to_tuple(color: QtGui.QColor) -> tuple[int, int, int]:
        return color.red(), color.green(), color.blue()

    def _plot_theme_text_qcolor(self) -> QtGui.QColor:
        return self.palette().color(QtGui.QPalette.ColorRole.Text)

    def _plot_channels(self) -> list[AcPlotChannel]:
        return [
            AcPlotChannel("elapsed_s", "Elapsed time [s]", "#ef4444", lambda point: point.elapsed_s),
            AcPlotChannel(
                "current_actual_mA",
                "Current measured [mA]",
                "#f97316",
                lambda point: point.current_actual_mA if point.current_actual_mA is not None else point.current_mA,
            ),
            AcPlotChannel("current_set_mA", "Current set [mA]", "#fb923c", lambda point: point.current_mA),
            AcPlotChannel("frequency_hz", "Frequency [Hz]", "#60a5fa", lambda point: point.frequency_hz),
            AcPlotChannel("amplitude_v", "Amplitude [V]", "#facc15", lambda point: point.amplitude_v),
            AcPlotChannel("rs_ohm", "Rs [Ohm]", "#14b8a6", lambda point: point.rs_ohm),
            AcPlotChannel("ls_h", "Ls [H]", "#a78bfa", lambda point: point.ls_h),
            AcPlotChannel("wire_resistance_ohm", "Wire R [Ohm]", "#f59e0b", lambda point: point.wire_resistance_ohm),
            AcPlotChannel("psu_power_w", "PSU power [W]", "#22c55e", lambda point: point.psu_power_w),
        ]

    def _plot_channel(self, key: str) -> AcPlotChannel | None:
        if key == "current_mA":
            key = "current_actual_mA"
        for channel in self._plot_channels():
            if channel.key == key:
                return channel
        return None

    @staticmethod
    def _compact_plot_label(label: str) -> str:
        return re.sub(r"\s*\[[^]]*\]", "", label).strip()

    def _plot_title(
        self,
        x_channel: AcPlotChannel,
        y_left_channel: AcPlotChannel,
        y_right_channel: AcPlotChannel | None,
        y_extra_channel: AcPlotChannel | None = None,
    ) -> str:
        left_label = self._compact_plot_label(y_left_channel.label)
        x_label = self._compact_plot_label(x_channel.label)
        if y_right_channel is None:
            return f"{left_label} vs {x_label}"
        right_label = self._compact_plot_label(y_right_channel.label)
        if y_extra_channel is None:
            return f"{left_label} + {right_label} vs {x_label}"
        extra_label = self._compact_plot_label(y_extra_channel.label)
        return f"{left_label} + {right_label} + {extra_label} vs {x_label}"

    def _handle_plot_config_changed(self) -> None:
        spread_combo = getattr(self, "comboBox_ac_plot_spread", None)
        if isinstance(spread_combo, QtWidgets.QComboBox):
            self.ac_settings.setValue("plot_x_spread", spread_combo.currentData() or "small")
        for index, tile in enumerate(self._plot_tiles):
            prefix = f"plot_tile_{index}"
            self.ac_settings.setValue(f"{prefix}_visible", tile.visible.isChecked())
            self.ac_settings.setValue(f"{prefix}_x", tile.x_combo.currentData() or "current_mA")
            self.ac_settings.setValue(f"{prefix}_y_left", tile.y_left_combo.currentData() or "rs_ohm")
            self.ac_settings.setValue(f"{prefix}_y_right", tile.y_right_combo.currentData() or "")
            self.ac_settings.setValue(f"{prefix}_y_extra", tile.y_extra_combo.currentData() or "")
        self._refresh_ac_plots()

    def _apply_plot_preset(self, preset: str) -> None:
        presets = {
            "current": [
                (True, "elapsed_s", "rs_ohm", "ls_h", ""),
                (True, "current_actual_mA", "rs_ohm", "ls_h", "wire_resistance_ohm"),
                (True, "frequency_hz", "rs_ohm", "ls_h", ""),
                (True, "amplitude_v", "rs_ohm", "ls_h", ""),
            ],
            "frequency": [
                (True, "frequency_hz", "rs_ohm", "ls_h", ""),
                (True, "amplitude_v", "rs_ohm", "ls_h", ""),
                (True, "elapsed_s", "rs_ohm", "ls_h", ""),
                (True, "current_actual_mA", "rs_ohm", "ls_h", "wire_resistance_ohm"),
            ],
        }
        for tile, (visible, x_key, y_left, y_right, y_extra) in zip(
            self._plot_tiles,
            presets.get(preset, presets["current"]),
        ):
            tile.visible.setChecked(visible)
            self._set_combo_data(tile.x_combo, x_key)
            self._set_combo_data(tile.y_left_combo, y_left)
            self._set_combo_data(tile.y_right_combo, y_right)
            self._set_combo_data(tile.y_extra_combo, y_extra)
        self._handle_plot_config_changed()

    def _plot_theme(self) -> dict[str, Any]:
        palette = self.palette()
        app = QtWidgets.QApplication.instance()
        style_hints = app.styleHints() if isinstance(app, QtWidgets.QApplication) else None
        color_scheme = style_hints.colorScheme() if style_hints is not None else QtCore.Qt.ColorScheme.Light
        window = palette.color(QtGui.QPalette.ColorRole.Window)
        base = palette.color(QtGui.QPalette.ColorRole.Base)
        text = palette.color(QtGui.QPalette.ColorRole.Text)
        mid = palette.color(QtGui.QPalette.ColorRole.Mid)
        grid = QtGui.QColor(mid)
        grid.setAlpha(160 if color_scheme == QtCore.Qt.ColorScheme.Dark else 120)
        return {
            "figure_rgb": window.getRgbF()[:3],
            "axes_rgb": base.getRgbF()[:3],
            "text_rgb": text.getRgbF()[:3],
            "grid_rgba": grid.getRgbF(),
        }

    def _show_plot_config_dialog(self) -> None:
        if self.plot_config_dialog is None:
            return
        if self.plot_config_dialog.isHidden():
            self.plot_config_dialog.show()
        self.plot_config_dialog.raise_()
        self.plot_config_dialog.activateWindow()

    def _detach_inherited_psu_settings(self) -> None:
        for combo in (
            getattr(self.ui, "comboBox_supply", None),
            getattr(self.ui, "comboBox_port", None),
            getattr(self.ui, "comboBox_baudrate", None),
        ):
            if isinstance(combo, QtWidgets.QComboBox):
                try:
                    combo.currentIndexChanged.disconnect()
                except TypeError:
                    pass
                combo.currentIndexChanged.connect(lambda *_args: self._handle_ac_psu_controls_changed())

    def _handle_ac_psu_controls_changed(self) -> None:
        if bool(getattr(self, "_ac_loading_settings", False)):
            return
        previous_backend = self._ac_psu_backend
        backend = self._backend_from_ac_supply_combo()
        if backend != previous_backend:
            self._store_ac_psu_profile_settings(previous_backend)
            self._ac_psu_backend = backend
            self._load_ac_psu_profile_settings(backend)
            self._apply_ac_psu_controls()
            self._apply_ac_psu_profile_state(self._ac_psu_backend)
            self._refresh_ac_psu_status()
            self.ac_settings.setValue("psu_backend", self._ac_psu_backend)
            self.ac_settings.setValue("psu_port", self._ac_psu_resource)
            self.ac_settings.setValue("psu_baud", str(self._ac_psu_baudrate))
            self.ac_settings.setValue("voltage_limit_v", float(self.spinBox_ac_voltage_limit.value()))
            self._store_ac_psu_profile_settings(self._ac_psu_backend)
            return
        else:
            self._capture_ac_psu_controls()
            self._store_ac_psu_profile_settings(self._ac_psu_backend)
        self._apply_ac_psu_profile_state(self._ac_psu_backend)
        self._refresh_ac_psu_status()
        self._store_lcr_settings()

    def _restore_ac_developer_settings(self) -> None:
        self._ac_diagnostics_enabled = bool(
            self.ac_settings.value("developer_diagnostics_enabled", False, type=bool)
        )
        self._ac_diagnostics_path = Path(
            self.ac_settings.value(
                "developer_diagnostics_path",
                str(AC_DIAGNOSTICS_DEFAULT_PATH),
                type=str,
            )
        )

    def _install_ac_developer_menu(self) -> None:
        menu_bar = self.menuBar()
        developer_menu: QtWidgets.QMenu | None = None
        for action in menu_bar.actions():
            menu = action.menu()
            if menu is not None and (
                menu.objectName() == "mw_shared_developer"
                or action.text().replace("&", "").lower() == "developer"
            ):
                developer_menu = menu
                break
        if developer_menu is None:
            developer_menu = menu_bar.addMenu("&Developer")
            developer_menu.setObjectName("mw_shared_developer")
        developer_menu.addSeparator()
        self.action_ac_diagnostics = developer_menu.addAction("Mirror AC Diagnostics to File")
        self.action_ac_diagnostics.setCheckable(True)
        self.action_ac_diagnostics.setChecked(self._ac_diagnostics_enabled)
        self.action_ac_diagnostics.toggled.connect(self._set_ac_diagnostics_enabled)
        choose_action = developer_menu.addAction("Choose AC Diagnostics File...")
        choose_action.triggered.connect(self._choose_ac_diagnostics_file)

    def _set_ac_diagnostics_enabled(self, enabled: bool) -> None:
        self._ac_diagnostics_enabled = bool(enabled)
        self.ac_settings.setValue("developer_diagnostics_enabled", self._ac_diagnostics_enabled)
        if self._ac_diagnostics_enabled:
            self._start_ac_ui_telemetry_timer()
        else:
            self._stop_ac_ui_telemetry_timer()
        self._write_ac_diagnostic("diagnostics_enabled", enabled=self._ac_diagnostics_enabled)

    def _start_ac_ui_telemetry_timer(self) -> None:
        try:
            existing = getattr(self, "_ac_ui_telemetry_timer", None)
        except RuntimeError:
            return
        if existing is not None:
            return
        self._ac_ui_telemetry_last_s = 0.0
        self._ac_ui_telemetry_ticks = 0
        self._ac_ui_telemetry_sum_s = 0.0
        self._ac_ui_telemetry_max_s = 0.0
        timer = QtCore.QTimer(self)
        timer.setInterval(AC_UI_TELEMETRY_INTERVAL_MS)
        timer.timeout.connect(self._record_ac_ui_telemetry_tick)
        timer.start()
        self._ac_ui_telemetry_timer = timer

    def _stop_ac_ui_telemetry_timer(self) -> None:
        try:
            timer = getattr(self, "_ac_ui_telemetry_timer", None)
        except RuntimeError:
            return
        if timer is not None:
            timer.stop()
        self._ac_ui_telemetry_timer = None
        self._ac_ui_telemetry_last_s = 0.0
        self._ac_ui_telemetry_ticks = 0
        self._ac_ui_telemetry_sum_s = 0.0
        self._ac_ui_telemetry_max_s = 0.0

    def _record_ac_ui_telemetry_tick(self) -> None:
        now = time.perf_counter()
        last = float(getattr(self, "_ac_ui_telemetry_last_s", 0.0))
        self._ac_ui_telemetry_last_s = now
        if last <= 0.0:
            return
        interval_s = max(0.0, now - last)
        self._ac_ui_telemetry_ticks = int(getattr(self, "_ac_ui_telemetry_ticks", 0)) + 1
        self._ac_ui_telemetry_sum_s = float(getattr(self, "_ac_ui_telemetry_sum_s", 0.0)) + interval_s
        self._ac_ui_telemetry_max_s = max(float(getattr(self, "_ac_ui_telemetry_max_s", 0.0)), interval_s)
        if self._ac_ui_telemetry_ticks < AC_UI_TELEMETRY_REPORT_TICKS:
            return
        total_s = max(1e-9, float(self._ac_ui_telemetry_sum_s))
        ticks = int(self._ac_ui_telemetry_ticks)
        self._write_ac_diagnostic(
            "ui_telemetry",
            ticks=ticks,
            fps_estimate=round(float(ticks) / total_s, 3),
            average_interval_s=round(total_s / float(ticks), 6),
            max_interval_s=round(float(self._ac_ui_telemetry_max_s), 6),
            target_interval_s=round(AC_UI_TELEMETRY_INTERVAL_MS / 1000.0, 6),
            last_plot_refresh_duration_s=round(float(getattr(self, "_ac_last_plot_refresh_duration_s", 0.0)), 6),
        )
        self._ac_ui_telemetry_ticks = 0
        self._ac_ui_telemetry_sum_s = 0.0
        self._ac_ui_telemetry_max_s = 0.0

    def _choose_ac_diagnostics_file(self) -> None:
        start = str(self._ac_diagnostics_path)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Choose AC diagnostics file",
            start,
            "JSON Lines (*.jsonl);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".jsonl"):
            path += ".jsonl"
        self._ac_diagnostics_path = Path(path)
        self.ac_settings.setValue("developer_diagnostics_path", str(self._ac_diagnostics_path))
        self._write_ac_diagnostic("diagnostics_path_changed", path=str(self._ac_diagnostics_path))

    def _write_ac_diagnostic(self, event: str, **payload: Any) -> None:
        try:
            enabled = bool(getattr(self, "_ac_diagnostics_enabled", False))
        except RuntimeError:
            enabled = False
        if not enabled:
            return
        try:
            path = Path(getattr(self, "_ac_diagnostics_path", AC_DIAGNOSTICS_DEFAULT_PATH))
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "event": event,
                **payload,
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass

    def _refresh_ac_plots(self, *, force: bool = False) -> None:
        if pg is not None and getattr(self, "_ac_pg_tiles", None):
            self._refresh_ac_pyqtgraph_plots(force=force)
            return
        self._refresh_ac_matplotlib_plots(force=force)

    def _refresh_ac_pyqtgraph_plots(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._ac_last_plot_refresh_monotonic < AC_PLOT_REFRESH_INTERVAL_S:
            return
        started = time.perf_counter()
        self._ac_last_plot_refresh_monotonic = now
        active_tiles = [tile for tile in self._plot_tiles if tile.visible.isChecked()]
        if not active_tiles and self._plot_tiles:
            active_tiles = list(self._plot_tiles[:1])
        if not active_tiles:
            return
        render_state: list[dict[str, Any]] = []
        for index, runtime in enumerate(self._ac_pg_tiles):
            if index >= len(active_tiles[:4]):
                runtime.widget.hide()
                render_state.append({})
                continue
            runtime.widget.show()
            tile = active_tiles[index]
            state = self._update_ac_pyqtgraph_tile(runtime, tile)
            render_state.append(state)
        self._ac_plot_render_state = render_state
        self._ac_last_plot_refresh_duration_s = max(0.0, time.perf_counter() - started)
        self._write_ac_diagnostic(
            "plot_refresh",
            backend="pyqtgraph",
            duration_s=round(self._ac_last_plot_refresh_duration_s, 6),
            points=len(getattr(self, "_ac_plot_points", [])),
            displayed_points=sum(len(state.get("left_xy", [])) for state in render_state),
            tiles=len(active_tiles[:4]),
        )

    def _update_ac_pyqtgraph_tile(
        self,
        runtime: AcPyQtGraphTile,
        tile: AcPlotTileWidgets,
    ) -> dict[str, Any]:
        x_channel = self._plot_channel(str(tile.x_combo.currentData() or "current_mA"))
        y_left_channel = self._plot_channel(str(tile.y_left_combo.currentData() or "rs_ohm"))
        y_right_channel = self._plot_channel(str(tile.y_right_combo.currentData() or ""))
        y_extra_channel = self._plot_channel(str(tile.y_extra_combo.currentData() or ""))
        plot_item = runtime.plot_item
        if x_channel is None or y_left_channel is None:
            runtime.left_item.setData([], [])
            runtime.right_item.setData([], [])
            runtime.extra_item.setData([], [])
            return {}
        x_log = x_channel.key == "frequency_hz"
        plot_item.setLogMode(x=x_log, y=False)
        runtime.left_item.setLogMode(x_log, False)
        runtime.right_item.setLogMode(x_log, False)
        runtime.extra_item.setLogMode(x_log, False)
        title = self._plot_title(x_channel, y_left_channel, y_right_channel, y_extra_channel)
        plot_item.setTitle(title, color=self._plot_theme_text_qcolor())
        plot_item.setLabel("bottom", x_channel.label, color=self._plot_theme_text_qcolor())
        self._style_pg_axis(plot_item.getAxis("bottom"), self._plot_theme_text_qcolor())
        plot_item.setLabel("left", y_left_channel.label, color=y_left_channel.color)
        self._style_pg_axis(plot_item.getAxis("left"), y_left_channel.color)
        if y_right_channel is not None:
            plot_item.setLabel("right", y_right_channel.label, color=y_right_channel.color)
            self._style_pg_axis(plot_item.getAxis("right"), y_right_channel.color)
            plot_item.getAxis("right").show()
        else:
            plot_item.setLabel("right", "")
            self._style_pg_axis(plot_item.getAxis("right"), self._plot_theme_text_qcolor())
            plot_item.getAxis("right").hide()
        if y_extra_channel is not None:
            runtime.extra_axis.setLabel(y_extra_channel.label, color=y_extra_channel.color)
            self._style_pg_axis(runtime.extra_axis, y_extra_channel.color)
            runtime.extra_axis.show()
        else:
            runtime.extra_axis.setLabel("")
            runtime.extra_axis.hide()
        points = self._display_points_for_plot(str(x_channel.key))
        left_pairs = self._plot_pairs(points, x_channel, y_left_channel)
        left_xy = self._pairs_with_plot_spread(plot_item, x_channel.key, left_pairs)
        self._set_pg_scatter(runtime.left_item, left_xy, y_left_channel.color, symbol="o")
        right_xy: list[tuple[float, float]] = []
        if y_right_channel is not None:
            right_pairs = self._plot_pairs(points, x_channel, y_right_channel)
            right_xy = self._pairs_with_plot_spread(plot_item, x_channel.key, right_pairs)
            self._set_pg_scatter(runtime.right_item, right_xy, y_right_channel.color, symbol="s")
        else:
            runtime.right_item.setData([], [])
        extra_xy: list[tuple[float, float]] = []
        extra_type = ""
        if y_extra_channel is not None:
            extra_pairs = (
                self._median_wire_resistance_pairs(points, x_channel)
                if y_extra_channel.key == "wire_resistance_ohm"
                else self._plot_pairs(points, x_channel, y_extra_channel)
            )
            extra_xy = self._pairs_with_plot_spread(plot_item, x_channel.key, extra_pairs)
            if y_extra_channel.key == "wire_resistance_ohm":
                extra_type = "line"
                self._set_pg_line(runtime.extra_item, extra_xy, y_extra_channel.color)
            else:
                extra_type = "scatter"
                self._set_pg_scatter(runtime.extra_item, extra_xy, y_extra_channel.color, symbol="t")
        else:
            runtime.extra_item.setData([], [])
        has_data = bool(left_xy or right_xy or extra_xy)
        runtime.no_data_item.setVisible(not has_data)
        if not has_data:
            plot_item.setRange(xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0.0)
            runtime.no_data_item.setPos(0.5, 0.5)
        else:
            plot_item.enableAutoRange()
            runtime.right_view.enableAutoRange()
            runtime.extra_view.enableAutoRange()
        self._update_pg_linked_views(runtime)
        return {
            "title": title,
            "x_key": x_channel.key,
            "x_label": x_channel.label,
            "x_log": x_log,
            "left_label": y_left_channel.label,
            "left_color": y_left_channel.color,
            "left_item_type": "scatter",
            "left_symbol_size": 4,
            "left_xy": left_xy,
            "right_label": y_right_channel.label if y_right_channel is not None else "",
            "right_color": y_right_channel.color if y_right_channel is not None else "",
            "right_item_type": "scatter" if y_right_channel is not None else "",
            "right_xy": right_xy,
            "extra_label": y_extra_channel.label if y_extra_channel is not None else "",
            "extra_color": y_extra_channel.color if y_extra_channel is not None else "",
            "extra_item_type": extra_type,
            "extra_xy": extra_xy,
            "show_legend": False,
            "grid": "left-only",
        }

    def _style_pg_axis(self, axis: Any, color: str | QtGui.QColor) -> None:
        axis.setPen(pg.mkPen(color))
        axis.setTextPen(pg.mkPen(color))
        try:
            axis.enableAutoSIPrefix(False)
        except AttributeError:
            pass

    def _set_pg_scatter(
        self,
        item: Any,
        xy: Sequence[tuple[float, float]],
        color: str,
        *,
        symbol: str,
    ) -> None:
        item.setSymbol(symbol)
        item.setPen(None)
        item.setSymbolSize(4)
        item.setSymbolBrush(pg.mkBrush(QtGui.QColor(color)))
        item.setSymbolPen(None)
        item.setData([x for x, _ in xy], [y for _, y in xy])

    def _set_pg_line(self, item: Any, xy: Sequence[tuple[float, float]], color: str) -> None:
        item.setPen(pg.mkPen(color, width=1.2))
        item.setSymbol("o")
        item.setSymbolSize(4)
        item.setSymbolBrush(pg.mkBrush(QtGui.QColor(color)))
        item.setSymbolPen(None)
        item.setData([x for x, _ in xy], [y for _, y in xy])

    def _pairs_with_plot_spread(
        self,
        axis: Any,
        x_key: str,
        pairs: Sequence[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if not pairs:
            return []
        x_values = [x_value for x_value, _ in pairs]
        jittered = self._jitter_plot_x_values(axis, str(x_key), x_values)
        return list(zip(jittered, [y_value for _, y_value in pairs]))

    def _refresh_ac_matplotlib_plots(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._ac_last_plot_refresh_monotonic < AC_PLOT_REFRESH_INTERVAL_S:
            return
        started = time.perf_counter()
        figure = getattr(self, "fig", None)
        if figure is None:
            return
        self._ac_last_plot_refresh_monotonic = now
        theme = self._plot_theme()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Attempt to set non-positive xlim on a log-scaled axis will be ignored.",
                category=UserWarning,
            )
            figure.clear()
        try:
            figure.set_layout_engine(None)
        except Exception:
            pass
        figure.set_facecolor(theme["figure_rgb"])
        active_tiles = [tile for tile in self._plot_tiles if tile.visible.isChecked()]
        if not active_tiles and self._plot_tiles:
            active_tiles = list(self._plot_tiles[:1])
        if not active_tiles:
            canvas = getattr(self, "canvas", None)
            if canvas is not None:
                canvas.draw_idle()
            return
        grid = figure.add_gridspec(2, 2, hspace=0.50, wspace=0.34)
        for tile_index, tile in enumerate(active_tiles[:4]):
            row, column = divmod(tile_index, 2)
            axis = figure.add_subplot(grid[row, column])
            self._style_ac_axis(axis, theme)
            x_channel = self._plot_channel(str(tile.x_combo.currentData() or "current_mA"))
            y_left_channel = self._plot_channel(str(tile.y_left_combo.currentData() or "rs_ohm"))
            y_right_channel = self._plot_channel(str(tile.y_right_combo.currentData() or ""))
            y_extra_channel = self._plot_channel(str(tile.y_extra_combo.currentData() or ""))
            if x_channel is None or y_left_channel is None:
                continue
            points = self._display_points_for_plot(str(x_channel.key))
            if x_channel.key == "frequency_hz":
                axis.set_xscale("log")
            axis.set_xlabel(x_channel.label, fontsize=9, labelpad=4)
            axis.set_ylabel(y_left_channel.label, fontsize=8, labelpad=3)
            self._color_ac_y_axis(axis, y_left_channel.color)
            axis.set_title(self._plot_title(x_channel, y_left_channel, y_right_channel, y_extra_channel), fontsize=9, pad=8)
            left_pairs = self._plot_pairs(points, x_channel, y_left_channel)
            if left_pairs:
                x_values = self._jitter_plot_x_values(
                    axis,
                    str(x_channel.key),
                    [x_value for x_value, _ in left_pairs],
                )
                y_values = [y_value for _, y_value in left_pairs]
                artist = axis.scatter(
                    x_values,
                    y_values,
                    color=y_left_channel.color,
                    s=5,
                    alpha=0.65,
                    label=y_left_channel.label,
                )
            else:
                axis.text(
                    0.5,
                    0.5,
                    "No AC data yet",
                    ha="center",
                    va="center",
                    color=theme["text_rgb"],
                    transform=axis.transAxes,
                )
            if y_right_channel is not None:
                twin = axis.twinx()
                self._style_ac_axis(twin, theme, grid=False)
                if x_channel.key == "frequency_hz":
                    twin.set_xscale("log")
                twin.set_ylabel(y_right_channel.label, fontsize=8, labelpad=3)
                self._color_ac_y_axis(twin, y_right_channel.color)
                right_pairs = self._plot_pairs(points, x_channel, y_right_channel)
                if right_pairs:
                    x_values = self._jitter_plot_x_values(
                        twin,
                        str(x_channel.key),
                        [x_value for x_value, _ in right_pairs],
                    )
                    y_values = [y_value for _, y_value in right_pairs]
                    artist = twin.scatter(
                        x_values,
                        y_values,
                        color=y_right_channel.color,
                        s=5,
                        marker="s",
                        alpha=0.65,
                        label=y_right_channel.label,
                    )
            if y_extra_channel is not None:
                extra_axis = axis.twinx()
                self._style_ac_axis(extra_axis, theme, grid=False)
                extra_axis.spines["right"].set_position(("axes", 1.20))
                extra_axis.spines["right"].set_visible(True)
                if x_channel.key == "frequency_hz":
                    extra_axis.set_xscale("log")
                extra_axis.set_ylabel(y_extra_channel.label, fontsize=8, labelpad=12)
                self._color_ac_y_axis(extra_axis, y_extra_channel.color)
                if y_extra_channel.key == "wire_resistance_ohm":
                    extra_pairs = self._median_wire_resistance_pairs(points, x_channel)
                else:
                    extra_pairs = self._plot_pairs(points, x_channel, y_extra_channel)
                if extra_pairs:
                    x_values = [x_value for x_value, _ in extra_pairs]
                    y_values = [y_value for _, y_value in extra_pairs]
                    if y_extra_channel.key == "wire_resistance_ohm":
                        artist = extra_axis.plot(
                            x_values,
                            y_values,
                            color=y_extra_channel.color,
                            marker="o",
                            markersize=3,
                            linewidth=1.1,
                            alpha=0.80,
                            label=y_extra_channel.label,
                        )[0]
                    else:
                        artist = extra_axis.scatter(
                            self._jitter_plot_x_values(extra_axis, str(x_channel.key), x_values),
                            y_values,
                            color=y_extra_channel.color,
                            s=5,
                            marker="^",
                            alpha=0.65,
                            label=y_extra_channel.label,
                        )
        figure.subplots_adjust(left=0.07, right=0.84, top=0.92, bottom=0.10, hspace=0.50, wspace=0.58)
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.draw_idle()
        self._ac_last_plot_refresh_duration_s = max(0.0, time.perf_counter() - started)
        self._write_ac_diagnostic(
            "plot_refresh",
            duration_s=round(self._ac_last_plot_refresh_duration_s, 6),
            points=len(getattr(self, "_ac_plot_points", [])),
            tiles=len(active_tiles[:4]),
        )

    @staticmethod
    def _plot_pairs(
        points: Sequence[AcPlotPoint],
        x_channel: AcPlotChannel,
        y_channel: AcPlotChannel,
    ) -> list[tuple[float, float]]:
        pairs = [(x_channel.getter(point), y_channel.getter(point)) for point in points]
        return [
            (float(x_value), float(y_value))
            for x_value, y_value in pairs
            if x_value is not None
            and y_value is not None
            and math.isfinite(float(x_value))
            and math.isfinite(float(y_value))
        ]

    def _median_wire_resistance_pairs(
        self,
        points: Sequence[AcPlotPoint],
        x_channel: AcPlotChannel,
    ) -> list[tuple[float, float]]:
        grouped: dict[tuple[str, float, float, float], list[tuple[float, float]]] = {}
        for point in points:
            x_value = x_channel.getter(point)
            y_value = point.wire_resistance_ohm
            if (
                x_value is None
                or y_value is None
                or not math.isfinite(float(x_value))
                or not math.isfinite(float(y_value))
            ):
                continue
            key = (
                point.model,
                round(float(point.frequency_hz), 12),
                round(float(point.amplitude_v), 12),
                round(float(point.current_mA), 12),
            )
            grouped.setdefault(key, []).append((float(x_value), float(y_value)))
        pairs = [
            (self._median([x_value for x_value, _ in values]), self._median([y_value for _, y_value in values]))
            for values in grouped.values()
            if values
        ]
        pairs.sort(key=lambda pair: pair[0])
        return pairs

    @staticmethod
    def _median(values: Sequence[float]) -> float:
        ordered = sorted(float(value) for value in values)
        if not ordered:
            return math.nan
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    def _jitter_plot_x_values(self, axis: Any, x_key: str, x_values: Sequence[float]) -> list[float]:
        values = [float(value) for value in x_values]
        if x_key not in {"current_actual_mA", "current_set_mA", "current_mA", "frequency_hz", "amplitude_v"}:
            return values
        spread_px = self._plot_spread_pixels()
        if spread_px <= 0.0:
            return values
        if len(values) < 2:
            return values
        groups: dict[float, list[int]] = {}
        for index, value in enumerate(values):
            groups.setdefault(round(value, 12), []).append(index)
        if all(len(indices) == 1 for indices in groups.values()):
            return values
        jittered = list(values)
        for indices in groups.values():
            if len(indices) < 2:
                continue
            if len(indices) == 2:
                offsets_px = [-spread_px, spread_px]
            else:
                step = (spread_px * 2.0) / float(len(indices) - 1)
                offsets_px = [-spread_px + step * offset_index for offset_index in range(len(indices))]
            for index, offset_px in zip(indices, offsets_px):
                jittered[index] = self._offset_x_by_display_px(axis, x_key, values[index], offset_px, values)
        return jittered

    def _plot_spread_pixels(self) -> float:
        combo = getattr(self, "comboBox_ac_plot_spread", None)
        key = "small"
        if isinstance(combo, QtWidgets.QComboBox):
            key = str(combo.currentData() or "small")
        return float(AC_PLOT_SPREAD_PIXELS.get(key, AC_PLOT_SPREAD_PIXELS["small"]))

    @staticmethod
    def _offset_x_by_display_px(axis: Any, x_key: str, x_value: float, offset_px: float, values: Sequence[float]) -> float:
        if offset_px == 0.0:
            return float(x_value)
        bbox_width = 300.0
        try:
            bbox_width = max(1.0, float(axis.get_window_extent().width))
        except Exception:
            pass
        finite_values = [float(value) for value in values if math.isfinite(float(value))]
        if not finite_values:
            return float(x_value)
        if x_key == "frequency_hz" and x_value > 0.0:
            positives = [value for value in finite_values if value > 0.0]
            if not positives:
                return float(x_value)
            log_min = math.log10(min(positives))
            log_max = math.log10(max(positives))
            log_span = max(0.10, log_max - log_min)
            return 10.0 ** (math.log10(float(x_value)) + (float(offset_px) / bbox_width) * log_span)
        min_value = min(finite_values)
        max_value = max(finite_values)
        span = max(max_value - min_value, abs(float(x_value)) * 0.04, 1.0)
        return float(x_value) + (float(offset_px) / bbox_width) * span

    def _display_points_for_plot(self, x_key: str) -> list[AcPlotPoint]:
        points = list(getattr(self, "_ac_plot_points", []))
        if x_key == "current_mA":
            x_key = "current_actual_mA"
        if x_key in {"elapsed_s", "current_actual_mA", "current_set_mA"}:
            return points[-AC_PLOT_RECENT_POINTS:]
        if x_key not in {"frequency_hz", "amplitude_v"}:
            return points[-AC_PLOT_RECENT_POINTS:]
        grouped: dict[tuple[str, float, float, float], list[AcPlotPoint]] = {}
        for point in points:
            key = (
                point.model,
                round(point.frequency_hz, 12),
                round(point.amplitude_v, 12),
                round(point.current_mA, 12),
            )
            grouped.setdefault(key, []).append(point)
        selected: list[AcPlotPoint] = []
        for group in grouped.values():
            selected.extend(self._thin_ac_plot_group(group, AC_PLOT_MAX_POINTS_PER_CONDITION))
        selected.sort(key=lambda point: point.elapsed_s)
        return selected

    @staticmethod
    def _thin_ac_plot_group(points: Sequence[AcPlotPoint], limit: int) -> list[AcPlotPoint]:
        values = list(points)
        limit = max(2, int(limit))
        if len(values) <= limit:
            return values
        if limit == 2:
            return [values[0], values[-1]]
        step = (len(values) - 1) / float(limit - 1)
        indices = sorted({int(round(i * step)) for i in range(limit)})
        return [values[index] for index in indices]

    def _start_ac_plot_refresh_timer(self) -> None:
        timer = QtCore.QTimer(self)
        timer.setInterval(int(round(AC_PLOT_REFRESH_INTERVAL_S * 1000.0)))
        timer.timeout.connect(self._refresh_dirty_ac_plots)
        timer.start()
        self._ac_plot_refresh_timer = timer

    def _refresh_dirty_ac_plots(self) -> None:
        try:
            dirty = bool(getattr(self, "_ac_plot_dirty"))
        except RuntimeError:
            return
        if not dirty:
            return
        self._ac_plot_dirty = False
        self._refresh_ac_plots(force=True)

    @staticmethod
    def _style_ac_axis(axis: Any, theme: dict[str, Any], *, grid: bool = True) -> None:
        axis.set_facecolor(theme["axes_rgb"])
        for spine in axis.spines.values():
            spine.set_color(theme["text_rgb"])
        axis.tick_params(colors=theme["text_rgb"])
        axis.xaxis.label.set_color(theme["text_rgb"])
        axis.yaxis.label.set_color(theme["text_rgb"])
        axis.title.set_color(theme["text_rgb"])
        if grid:
            axis.grid(True, color=theme["grid_rgba"], alpha=0.6)
        else:
            axis.grid(False)

    @staticmethod
    def _color_ac_y_axis(axis: Any, color: str) -> None:
        axis.yaxis.label.set_color(color)
        axis.tick_params(axis="y", colors=color)
        try:
            axis.spines["left"].set_color(color)
        except Exception:
            pass
        try:
            axis.spines["right"].set_color(color)
        except Exception:
            pass

    def _set_default_log_name(self) -> None:
        line_edit = getattr(self.ui, "lineEdit_log_file", None)
        if isinstance(line_edit, QtWidgets.QLineEdit):
            current = line_edit.text().strip()
            if not current or current in AC_LEGACY_INHERITED_BASES:
                line_edit.setText(AC_DEFAULT_SWEEP_BASE)
                self.sync_full_log_path()

    def _load_ac_output_settings(self) -> None:
        directory = str(self.ac_settings.value("log_dir", "", type=str) or "").strip()
        inherited_directory = ""
        inherited_base = ""
        try:
            inherited_directory = str(self.settings.value("log_dir", "", type=str) or "").strip()
            inherited_base = str(self.settings.value("log_file", "", type=str) or "").strip()
        except Exception:
            pass
        if directory and inherited_directory and Path(directory) == Path(inherited_directory):
            directory = ""
        if not directory:
            directory = str(AC_DEFAULT_LOG_DIR)
        base = str(self.ac_settings.value("log_file", "", type=str) or "").strip()
        if base and inherited_base and base == inherited_base and not base.startswith("ac_susc"):
            base = ""
        if not base or base in AC_LEGACY_INHERITED_BASES:
            base = AC_DEFAULT_SWEEP_BASE
        dir_edit = getattr(self.ui, "lineEdit_log_dir", None)
        file_edit = getattr(self.ui, "lineEdit_log_file", None)
        if isinstance(dir_edit, QtWidgets.QLineEdit):
            dir_edit.setText(directory)
        if isinstance(file_edit, QtWidgets.QLineEdit):
            file_edit.setText(base)
        self._ac_output_settings_ready = True
        self.sync_full_log_path()

    def sync_full_log_path(self) -> None:  # type: ignore[override]
        full = self.build_log_path()
        hidden = getattr(self.ui, "lineEdit_log_file_full", None)
        if isinstance(hidden, QtWidgets.QLineEdit):
            hidden.setText(full)
        self.f_name = full
        try:
            ready = bool(getattr(self, "_ac_output_settings_ready", False))
        except RuntimeError:
            ready = False
        if not ready:
            return
        try:
            directory = self.ui.lineEdit_log_dir.text().strip()
            base = self.ui.lineEdit_log_file.text().strip()
            if directory:
                self.ac_settings.setValue("log_dir", directory)
            if base:
                self.ac_settings.setValue("log_file", base)
        except Exception:
            pass

    def build_log_path(self) -> str:  # type: ignore[override]
        try:
            directory = self.ui.lineEdit_log_dir.text().strip() or str(AC_DEFAULT_LOG_DIR)
            base = self.ui.lineEdit_log_file.text().strip() or AC_DEFAULT_SWEEP_BASE
            if base in AC_LEGACY_INHERITED_BASES:
                base = AC_DEFAULT_SWEEP_BASE
            if base.lower().endswith(".tsv"):
                base = base[:-4]
            os.makedirs(directory, exist_ok=True)
            return os.path.join(directory, f"{base}.tsv")
        except Exception:
            return str(AC_DEFAULT_LOG_DIR / f"{AC_DEFAULT_SWEEP_BASE}.tsv")

    def handle_browse_log_dir(self) -> None:  # type: ignore[override]
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, "lineEdit_log_dir") else str(AC_DEFAULT_LOG_DIR)
        new_dir = QtWidgets.QFileDialog.getExistingDirectory(self, "Select AC susceptibility output directory", start_dir)
        if new_dir and hasattr(self.ui, "lineEdit_log_dir"):
            self.ui.lineEdit_log_dir.setText(new_dir)
            self.ac_settings.setValue("log_dir", new_dir)
            self.sync_full_log_path()

    def handle_browse_full_file(self) -> None:  # type: ignore[override]
        start_dir = self.ui.lineEdit_log_dir.text() if hasattr(self.ui, "lineEdit_log_dir") else str(AC_DEFAULT_LOG_DIR)
        fpath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save AC susceptibility sweep as",
            start_dir,
            "TSV files (*.tsv);;All files (*)",
        )
        if not fpath:
            return
        if not fpath.lower().endswith(".tsv"):
            fpath += ".tsv"
        directory = os.path.dirname(fpath)
        base = os.path.splitext(os.path.basename(fpath))[0] or AC_DEFAULT_SWEEP_BASE
        if hasattr(self.ui, "lineEdit_log_dir"):
            self.ui.lineEdit_log_dir.setText(directory)
        if hasattr(self.ui, "lineEdit_log_file"):
            self.ui.lineEdit_log_file.setText(base)
        hidden = getattr(self.ui, "lineEdit_log_file_full", None)
        if isinstance(hidden, QtWidgets.QLineEdit):
            hidden.setText(fpath)
        self.ac_settings.setValue("log_dir", directory)
        self.ac_settings.setValue("log_file", base)
        self.f_name = fpath

    def handle_select_filename_en(self) -> None:  # type: ignore[override]
        self.handle_browse_full_file()

    def _install_lcr_controls(self) -> None:
        frame = getattr(self.ui, "frame_serial_settings", None)
        layout = frame.layout() if isinstance(frame, QtWidgets.QWidget) else None
        if layout is None:
            return

        serial_group = getattr(self.ui, "groupBox_serial_settings", None)
        if isinstance(serial_group, QtWidgets.QGroupBox):
            serial_group.hide()

        group = QtWidgets.QGroupBox("Instrument setup", frame)
        outer = QtWidgets.QVBoxLayout(group)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        output_group = QtWidgets.QGroupBox("Output", group)
        output_grid = QtWidgets.QGridLayout(output_group)
        output_grid.setColumnStretch(1, 1)
        output_grid.addWidget(getattr(self.ui, "label_log_dir"), 0, 0)
        output_grid.addWidget(getattr(self.ui, "lineEdit_log_dir"), 0, 1)
        output_buttons = QtWidgets.QHBoxLayout()
        output_buttons.setContentsMargins(0, 0, 0, 0)
        output_buttons.addWidget(getattr(self.ui, "pushButton_open_dir"))
        output_buttons.addWidget(getattr(self.ui, "pushButton_browse_dir"))
        output_grid.addLayout(output_buttons, 0, 2)
        output_grid.addWidget(getattr(self.ui, "label_log_file"), 1, 0)
        log_file_label = getattr(self.ui, "label_log_file", None)
        if isinstance(log_file_label, QtWidgets.QLabel):
            log_file_label.setText("Microwire sweep base:")
        extension_label = getattr(self.ui, "label_extension", None)
        if isinstance(extension_label, QtWidgets.QLabel):
            extension_label.setText(".tsv")
        file_row = QtWidgets.QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.addWidget(getattr(self.ui, "lineEdit_log_file"), 1)
        file_row.addWidget(getattr(self.ui, "label_extension"))
        output_grid.addLayout(file_row, 1, 1, 1, 2)
        self.label_ac_baseline_file = QtWidgets.QLabel(
            "Empty-coil baseline: ac_susc_empty_coil_baseline_<timestamp>.tsv",
            output_group,
        )
        self.label_ac_baseline_file.setWordWrap(True)
        output_grid.addWidget(self.label_ac_baseline_file, 2, 1, 1, 2)
        outer.addWidget(output_group)

        hardware_group = QtWidgets.QGroupBox("Hardware", group)
        hardware_layout = QtWidgets.QVBoxLayout(hardware_group)
        hardware_layout.setContentsMargins(8, 8, 8, 8)
        hardware_layout.setSpacing(8)
        hardware_top = QtWidgets.QHBoxLayout()
        hardware_top.setContentsMargins(0, 0, 0, 0)
        self.label_ac_hardware_status = QtWidgets.QLabel("Hardware not connected", hardware_group)
        self.label_ac_hardware_status.setWordWrap(True)
        self.pushButton_auto_setup = QtWidgets.QPushButton("Auto-connect hardware", hardware_group)
        self.pushButton_ac_hardware_details = QtWidgets.QToolButton(hardware_group)
        self.pushButton_ac_hardware_details.setText("Show hardware details")
        self.pushButton_ac_hardware_details.setCheckable(True)
        self.pushButton_ac_hardware_details.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        hardware_top.addWidget(self.label_ac_hardware_status, stretch=1)
        hardware_top.addWidget(self.pushButton_auto_setup)
        hardware_top.addWidget(self.pushButton_ac_hardware_details)
        hardware_layout.addLayout(hardware_top)

        self.frame_ac_hardware_details = QtWidgets.QFrame(hardware_group)
        details_grid = QtWidgets.QGridLayout(self.frame_ac_hardware_details)
        details_grid.setContentsMargins(0, 0, 0, 0)
        details_grid.setColumnStretch(1, 1)
        details_grid.setColumnStretch(3, 1)
        supply_combo = getattr(self.ui, "comboBox_supply", None)
        psu_port_combo = getattr(self.ui, "comboBox_port", None)
        baud_combo = getattr(self.ui, "comboBox_baudrate", None)
        if isinstance(supply_combo, QtWidgets.QComboBox):
            details_grid.addWidget(QtWidgets.QLabel("PSU:", self.frame_ac_hardware_details), 0, 0)
            details_grid.addWidget(supply_combo, 0, 1)
        if isinstance(psu_port_combo, QtWidgets.QComboBox):
            details_grid.addWidget(QtWidgets.QLabel("PSU port:", self.frame_ac_hardware_details), 0, 2)
            details_grid.addWidget(psu_port_combo, 0, 3)
        if isinstance(baud_combo, QtWidgets.QComboBox):
            details_grid.addWidget(QtWidgets.QLabel("Baud:", self.frame_ac_hardware_details), 1, 0)
            details_grid.addWidget(baud_combo, 1, 1)
        connect_port_button = getattr(self.ui, "pushButton_connect_port", None)
        if isinstance(connect_port_button, QtWidgets.QPushButton):
            connect_port_button.hide()
        self.comboBox_lcr_port = QtWidgets.QComboBox(group)
        self.pushButton_refresh_lcr_ports = QtWidgets.QPushButton("Refresh", group)
        self.pushButton_connect_lcr = QtWidgets.QPushButton("Connect LCR", group)
        self.pushButton_identify_lcr = QtWidgets.QPushButton("Identify", group)
        details_grid.addWidget(QtWidgets.QLabel("LCR port:", self.frame_ac_hardware_details), 1, 2)
        details_grid.addWidget(self.comboBox_lcr_port, 1, 3)
        lcr_button_row = QtWidgets.QHBoxLayout()
        lcr_button_row.setContentsMargins(0, 0, 0, 0)
        lcr_button_row.addWidget(self.pushButton_refresh_lcr_ports)
        lcr_button_row.addWidget(self.pushButton_connect_lcr)
        lcr_button_row.addWidget(self.pushButton_identify_lcr)
        details_grid.addLayout(lcr_button_row, 2, 2, 1, 2)
        self.pushButton_identify_lcr.hide()
        self.frame_ac_hardware_details.hide()
        hardware_layout.addWidget(self.frame_ac_hardware_details)
        outer.addWidget(hardware_group)
        self.groupBox_ac_hardware = hardware_group

        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.lineEdit_lcr_frequencies = QtWidgets.QLineEdit(group)
        self.lineEdit_lcr_levels = QtWidgets.QLineEdit(group)
        self.comboBox_lcr_level_mode = QtWidgets.QComboBox(group)
        self.comboBox_lcr_level_mode.addItem("Voltage", "voltage")
        self.comboBox_lcr_level_mode.hide()
        self.comboBox_lcr_function = QtWidgets.QComboBox(group)
        self.comboBox_lcr_function.addItems(list(SUPPORTED_FUNCTIONS))
        self.comboBox_lcr_function.setCurrentText("Ls-Rs")
        self.comboBox_lcr_function.hide()
        self.checkBox_lcr_model_lsrs = QtWidgets.QCheckBox("Ls-Rs", group)
        self.checkBox_lcr_model_lsrs.hide()
        self.checkBox_lcr_model_lprp = QtWidgets.QCheckBox("Lp-Rp", group)
        self.checkBox_lcr_model_lprp.setText("Also measure Lp-Rp")
        self.checkBox_lcr_model_lsrs.setChecked(True)
        self.comboBox_lcr_monitor1 = QtWidgets.QComboBox(group)
        self.comboBox_lcr_monitor1.addItems(list(SUPPORTED_MONITORS))
        self.comboBox_lcr_monitor2 = QtWidgets.QComboBox(group)
        self.comboBox_lcr_monitor2.addItems(list(SUPPORTED_MONITORS))
        self.comboBox_lcr_aperture = QtWidgets.QComboBox(group)
        self.comboBox_lcr_aperture.addItems(["FAST", "MED", "SLOW"])
        self.checkBox_ac_plan_loops = QtWidgets.QCheckBox("One current sweep per AC setting", group)
        self.checkBox_ac_plan_loops.setChecked(True)
        self.checkBox_ac_plan_loops.hide()
        self.pushButton_apply_lcr_setting = QtWidgets.QPushButton("Apply setting", group)
        self.pushButton_apply_lcr_setting.hide()
        self.pushButton_lcr_default_presets = QtWidgets.QPushButton("Default full scan", group)
        self.pushButton_lcr_all_frequencies = QtWidgets.QPushButton("All practical frequencies", group)
        self.pushButton_lcr_all_levels = QtWidgets.QPushButton("All amplitudes", group)
        self.pushButton_measure_lcr_baseline = QtWidgets.QPushButton("Measure empty-coil baseline", group)
        self.label_ac_psu_status = QtWidgets.QLabel("", group)
        self.label_ac_psu_status.setWordWrap(True)
        self.spinBox_ac_voltage_limit = CompactDoubleSpinBox(group)
        self.spinBox_ac_voltage_limit.setRange(0.1, 120.0)
        self.spinBox_ac_voltage_limit.setDecimals(3)
        self.spinBox_ac_voltage_limit.setSuffix(" V")
        self.spinBox_ac_voltage_limit.setValue(OWON_DEFAULT_VOLTAGE_LIMIT_V)
        self.spinBox_ac_current_start = CompactDoubleSpinBox(group)
        self.spinBox_ac_current_start.setRange(0.0, 10000.0)
        self.spinBox_ac_current_start.setDecimals(3)
        self.spinBox_ac_current_start.setSuffix(" mA")
        self.spinBox_ac_current_start.setValue(20.0)
        self.spinBox_ac_current_stop = CompactDoubleSpinBox(group)
        self.spinBox_ac_current_stop.setRange(0.0, 10000.0)
        self.spinBox_ac_current_stop.setDecimals(3)
        self.spinBox_ac_current_stop.setSuffix(" mA")
        self.spinBox_ac_current_stop.setValue(80.0)
        self.spinBox_ac_current_step = CompactDoubleSpinBox(group)
        self.spinBox_ac_current_step.setRange(0.001, 10000.0)
        self.spinBox_ac_current_step.setDecimals(3)
        self.spinBox_ac_current_step.setSuffix(" mA")
        self.spinBox_ac_current_step.setValue(5.0)
        self.comboBox_ac_direction = QtWidgets.QComboBox(group)
        self.comboBox_ac_direction.addItem("Up and down", "up-down")
        self.comboBox_ac_direction.addItem("Up only", "up")
        self.comboBox_ac_direction.addItem("Down only", "down")
        self.checkBox_ac_include_zero_current = QtWidgets.QCheckBox("Also measure 0 mA reference", group)
        self.checkBox_ac_include_zero_current.setToolTip(
            "Add one no-current point before the PSU current loop. Useful because the OWON cannot regulate below about 10 mA."
        )
        self.spinBox_ac_dwell = CompactDoubleSpinBox(group)
        self.spinBox_ac_dwell.setRange(0.0, 3600.0)
        self.spinBox_ac_dwell.setDecimals(3)
        self.spinBox_ac_dwell.setSuffix(" s")
        self.spinBox_ac_dwell.setValue(1.0)
        self.spinBox_ac_point_duration = CompactDoubleSpinBox(group)
        self.spinBox_ac_point_duration.setRange(0.1, 3600.0)
        self.spinBox_ac_point_duration.setDecimals(3)
        self.spinBox_ac_point_duration.setSuffix(" s")
        self.spinBox_ac_point_duration.setValue(10.0)
        self.label_ac_sweep_estimate = QtWidgets.QLabel("", group)
        self.label_ac_sweep_estimate.setWordWrap(True)
        self.pushButton_run_ac_sweep = QtWidgets.QPushButton("Run microwire current sweep", group)
        self.pushButton_stop_ac_sweep = QtWidgets.QPushButton("Stop", group)
        self.pushButton_stop_ac_sweep.setEnabled(False)

        self.lineEdit_lcr_frequencies.setPlaceholderText("10, 20, 50, 100, 200, 500, 1k, 2k, 5k, 10k, 20k, 50k, 100k, 200k")
        self.lineEdit_lcr_levels.setPlaceholderText("0.01, 0.1, 0.3, 0.5, 1.0, 1.5, 2.0")

        grid.addWidget(QtWidgets.QLabel("Frequencies:", group), 0, 0)
        grid.addWidget(self.lineEdit_lcr_frequencies, 0, 1, 1, 3)
        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(self.pushButton_lcr_default_presets)
        preset_row.addWidget(self.pushButton_lcr_all_frequencies)
        preset_row.addWidget(self.pushButton_lcr_all_levels)
        preset_row.addStretch(1)
        grid.addLayout(preset_row, 1, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Amplitudes:", group), 2, 0)
        grid.addWidget(self.lineEdit_lcr_levels, 2, 1, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Model:", group), 3, 0)
        model_label = QtWidgets.QLabel("Ls-Rs", group)
        model_label.setToolTip("Recommended default for the overnight AC susceptibility workflow.")
        grid.addWidget(model_label, 3, 1)
        grid.addWidget(self.checkBox_lcr_model_lprp, 3, 2, 1, 2)
        grid.addWidget(QtWidgets.QLabel("LCR speed:", group), 4, 0)
        grid.addWidget(self.comboBox_lcr_aperture, 4, 1)
        grid.addWidget(QtWidgets.QLabel("Monitor 1:", group), 4, 2)
        grid.addWidget(self.comboBox_lcr_monitor1, 4, 3)
        self.comboBox_lcr_monitor2.hide()
        grid.addWidget(self.comboBox_lcr_level_mode, 5, 0)
        grid.addWidget(self.comboBox_lcr_function, 5, 1)
        grid.addWidget(self.checkBox_lcr_model_lsrs, 5, 2)
        grid.addWidget(self.checkBox_ac_plan_loops, 5, 3)
        grid.addWidget(self.pushButton_apply_lcr_setting, 6, 3)
        outer.addLayout(grid)

        plan_group = QtWidgets.QGroupBox("Experiment plan", group)
        plan_grid = QtWidgets.QGridLayout(plan_group)
        plan_grid.setColumnStretch(1, 1)
        plan_grid.setColumnStretch(3, 1)
        self.label_ac_voltage_limit = QtWidgets.QLabel("Voltage limit:", plan_group)
        self.label_ac_current_start = QtWidgets.QLabel("Current start:", plan_group)
        self.label_ac_current_stop = QtWidgets.QLabel("Current stop:", plan_group)
        self.label_ac_current_step = QtWidgets.QLabel("Current step:", plan_group)
        self.label_ac_direction = QtWidgets.QLabel("Direction:", plan_group)
        self.label_ac_settle_time = QtWidgets.QLabel("Settle time:", plan_group)
        self.label_ac_point_duration = QtWidgets.QLabel("Measure time/point:", plan_group)
        plan_grid.addWidget(self.label_ac_voltage_limit, 0, 0)
        plan_grid.addWidget(self.spinBox_ac_voltage_limit, 0, 1)
        plan_grid.addWidget(self.label_ac_point_duration, 0, 2)
        plan_grid.addWidget(self.spinBox_ac_point_duration, 0, 3)
        plan_grid.addWidget(self.label_ac_current_start, 1, 0)
        plan_grid.addWidget(self.spinBox_ac_current_start, 1, 1)
        plan_grid.addWidget(self.label_ac_current_stop, 1, 2)
        plan_grid.addWidget(self.spinBox_ac_current_stop, 1, 3)
        plan_grid.addWidget(self.label_ac_current_step, 2, 0)
        plan_grid.addWidget(self.spinBox_ac_current_step, 2, 1)
        plan_grid.addWidget(self.label_ac_direction, 2, 2)
        plan_grid.addWidget(self.comboBox_ac_direction, 2, 3)
        plan_grid.addWidget(self.label_ac_settle_time, 3, 0)
        plan_grid.addWidget(self.spinBox_ac_dwell, 3, 1)
        plan_grid.addWidget(self.checkBox_ac_include_zero_current, 3, 2, 1, 2)
        plan_grid.addWidget(self.label_ac_sweep_estimate, 4, 0, 1, 4)
        action_row = QtWidgets.QHBoxLayout()
        action_row.addWidget(self.pushButton_measure_lcr_baseline)
        action_row.addWidget(self.pushButton_run_ac_sweep)
        action_row.addWidget(self.pushButton_stop_ac_sweep)
        self.frame_ac_plan_actions = QtWidgets.QFrame(plan_group)
        self.frame_ac_plan_actions.setLayout(action_row)
        self.frame_ac_plan_actions.hide()
        plan_grid.addWidget(self.frame_ac_plan_actions, 5, 0, 1, 4)
        outer.addWidget(plan_group)
        self.groupBox_ac_plan = plan_group

        self.label_lcr_status = QtWidgets.QLabel("LCR not connected", group)
        self.label_lcr_status.setWordWrap(True)
        outer.addWidget(self.label_lcr_status)

        cast(QtWidgets.QVBoxLayout, layout).addWidget(group)
        self.groupBox_lcr_settings = group

        self.pushButton_refresh_lcr_ports.clicked.connect(self.populate_lcr_ports)
        self.pushButton_connect_lcr.clicked.connect(self.handle_connect_lcr_clicked)
        self.pushButton_identify_lcr.clicked.connect(self.handle_identify_lcr_clicked)
        self.pushButton_auto_setup.clicked.connect(self.handle_auto_setup_clicked)
        self.pushButton_ac_hardware_details.toggled.connect(self._set_ac_hardware_details_visible)
        self.pushButton_apply_lcr_setting.clicked.connect(self.handle_apply_lcr_setting_clicked)
        self.pushButton_measure_lcr_baseline.clicked.connect(self.handle_measure_lcr_baseline_clicked)
        self.pushButton_run_ac_sweep.clicked.connect(self.handle_run_ac_sweep_clicked)
        self.pushButton_stop_ac_sweep.clicked.connect(self.handle_stop_ac_sweep_clicked)
        self.pushButton_lcr_default_presets.clicked.connect(self.apply_default_lcr_presets)
        self.pushButton_lcr_all_frequencies.clicked.connect(self.apply_all_lcr_frequencies)
        self.pushButton_lcr_all_levels.clicked.connect(self.apply_all_lcr_levels)
        for shared in (
            getattr(self.ui, "comboBox_supply", None),
            getattr(self.ui, "comboBox_port", None),
            getattr(self.ui, "comboBox_baudrate", None),
        ):
            if isinstance(shared, QtWidgets.QComboBox):
                shared.currentIndexChanged.connect(lambda *_args: self._sync_ac_psu_from_shared_controls())
        for edit in (self.lineEdit_lcr_frequencies, self.lineEdit_lcr_levels):
            edit.editingFinished.connect(self._store_lcr_settings)
            edit.editingFinished.connect(self._update_ac_sweep_estimate)
        for combo in (
            self.comboBox_lcr_level_mode,
            self.comboBox_lcr_function,
            self.comboBox_lcr_monitor1,
            self.comboBox_lcr_monitor2,
            self.comboBox_lcr_aperture,
        ):
            combo.currentIndexChanged.connect(lambda *_args: self._store_lcr_settings())
            combo.currentIndexChanged.connect(lambda *_args: self._update_ac_sweep_estimate())
        self.checkBox_ac_plan_loops.toggled.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_ac_include_zero_current.toggled.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_ac_include_zero_current.toggled.connect(lambda *_args: self._update_ac_sweep_estimate())
        self.checkBox_lcr_model_lsrs.toggled.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_lcr_model_lprp.toggled.connect(lambda *_args: self._store_lcr_settings())
        self.checkBox_lcr_model_lsrs.toggled.connect(lambda *_args: self._update_ac_sweep_estimate())
        self.checkBox_lcr_model_lprp.toggled.connect(lambda *_args: self._update_ac_sweep_estimate())
        for widget in (
            self.spinBox_ac_voltage_limit,
            self.spinBox_ac_current_start,
            self.spinBox_ac_current_stop,
            self.spinBox_ac_current_step,
            self.comboBox_ac_direction,
            self.spinBox_ac_dwell,
            self.spinBox_ac_point_duration,
        ):
            signal = getattr(widget, "currentIndexChanged", None) or getattr(widget, "valueChanged", None)
            if signal is not None:
                signal.connect(lambda *_args: self._store_lcr_settings())
                signal.connect(lambda *_args: self._update_ac_sweep_estimate())
        self._install_ac_wheel_guard(group)

    def _simplify_inherited_ac_workflow(self) -> None:
        process_frame = getattr(self.ui, "frame_process_settings", None)
        if isinstance(process_frame, QtWidgets.QWidget):
            process_frame.hide()
        command_frame = getattr(self.ui, "frame_command_and_response", None)
        if isinstance(command_frame, QtWidgets.QWidget):
            command_frame.hide()
        button_map: tuple[tuple[str, str, Any], ...] = (
            ("pushButton_start_process", "Measure empty-coil baseline", self.handle_measure_lcr_baseline_clicked),
            ("pushButton_show_history", "Run microwire current sweep", self.handle_run_ac_sweep_clicked),
            ("pushButton_reverse_now", "Stop", self.handle_stop_ac_sweep_clicked),
        )
        for attr, text, slot in button_map:
            button = getattr(self.ui, attr, None)
            if not isinstance(button, QtWidgets.QPushButton):
                continue
            button.setText(text)
            button.setEnabled(text != "Stop")
            try:
                button.clicked.disconnect()
            except TypeError:
                pass
            button.clicked.connect(slot)

    def _install_ac_sticky_progress(self) -> None:
        start_button = getattr(self.ui, "pushButton_start_process", None)
        button_frame = start_button.parentWidget() if isinstance(start_button, QtWidgets.QPushButton) else None
        container = button_frame.parentWidget() if isinstance(button_frame, QtWidgets.QWidget) else None
        layout = container.layout() if isinstance(container, QtWidgets.QWidget) else None
        if layout is None or button_frame is None:
            return
        self.label_ac_current_task = QtWidgets.QLabel("Current task: idle", container)
        self.label_ac_current_task.setWordWrap(True)
        self.progress_ac_run = QtWidgets.QProgressBar(container)
        self.progress_ac_run.setRange(0, 100)
        self.progress_ac_run.setValue(0)
        self.progress_ac_run.setTextVisible(True)
        self.progress_ac_run.setFormat("AC progress: idle")
        button_frame_index = layout.indexOf(button_frame)
        if button_frame_index >= 0:
            layout.insertWidget(button_frame_index, self.label_ac_current_task)
            button_frame_index = layout.indexOf(button_frame)
            layout.insertWidget(button_frame_index, self.progress_ac_run)
        else:
            layout.addWidget(self.label_ac_current_task)
            layout.addWidget(self.progress_ac_run)

    def _load_lcr_settings(self) -> None:
        self._ac_loading_settings = True
        try:
            self.lineEdit_lcr_frequencies.setText(
                self.ac_settings.value("frequencies", self._format_numeric_list(DEFAULT_FREQUENCY_PRESETS_HZ), type=str)
            )
            self.lineEdit_lcr_levels.setText(
                self.ac_settings.value("levels", self._format_numeric_list(LCR_FRONT_PANEL_VOLTAGE_PRESETS_V), type=str)
            )
            self._set_combo_data(self.comboBox_lcr_level_mode, self.ac_settings.value("level_mode", "voltage", type=str))
            self._set_combo_text(self.comboBox_lcr_function, "Ls-Rs")
            models = str(self.ac_settings.value("models", "Ls-Rs", type=str))
            model_tokens = {token.strip().lower() for token in re.split(r"[,;\s]+", models) if token.strip()}
            self.checkBox_lcr_model_lsrs.setChecked(True)
            self.checkBox_lcr_model_lprp.setChecked("lp-rp" in model_tokens)
            self._set_combo_text(self.comboBox_lcr_monitor1, self.ac_settings.value("monitor1", "Z", type=str))
            self._set_combo_text(self.comboBox_lcr_monitor2, self.ac_settings.value("monitor2", "IAC", type=str))
            self._set_combo_text(self.comboBox_lcr_aperture, self.ac_settings.value("aperture", "FAST", type=str))
            stored_frequencies = self.lineEdit_lcr_frequencies.text().strip()
            if stored_frequencies in LEGACY_DEFAULT_FREQUENCY_TEXTS:
                self.lineEdit_lcr_frequencies.setText(self._format_numeric_list(PRACTICAL_FREQUENCY_PRESETS_HZ))
            stored_levels = self.lineEdit_lcr_levels.text().strip()
            if not stored_levels or stored_levels in LEGACY_DEFAULT_LEVEL_TEXTS:
                self.lineEdit_lcr_levels.setText(self._format_numeric_list(LCR_FRONT_PANEL_VOLTAGE_PRESETS_V))
            plan_loops = self.ac_settings.value("plan_loops", 1)
            self.checkBox_ac_plan_loops.setChecked(str(plan_loops).lower() not in {"0", "false", "no"})
            self._set_combo_data(self.comboBox_ac_direction, self.ac_settings.value("direction", "up-down", type=str))
            self.spinBox_ac_current_start.setValue(float(self.ac_settings.value("current_start_mA", 20.0)))
            self.spinBox_ac_current_stop.setValue(float(self.ac_settings.value("current_stop_mA", 80.0)))
            self.spinBox_ac_current_step.setValue(float(self.ac_settings.value("current_step_mA", 5.0)))
            self.spinBox_ac_dwell.setValue(float(self.ac_settings.value("dwell_s", 1.0)))
            point_duration = self.ac_settings.value("point_duration_s", None)
            if point_duration is None:
                legacy_repeats = float(self.ac_settings.value("sweep_repeats", 10))
                point_duration = max(1.0, legacy_repeats)
            self.spinBox_ac_point_duration.setValue(float(point_duration))
            self._load_ac_psu_settings()
            self._apply_ac_psu_controls()
            self._refresh_ac_psu_status()
            include_zero = self.ac_settings.value("include_zero_current", 0)
            self.checkBox_ac_include_zero_current.setChecked(str(include_zero).lower() in {"1", "true", "yes"})
        finally:
            self._ac_loading_settings = False

    @staticmethod
    def _set_combo_text(combo: QtWidgets.QComboBox, text: str) -> None:
        idx = combo.findText(text, QtCore.Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _set_combo_data(combo: QtWidgets.QComboBox, data: str) -> None:
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _store_lcr_settings(self) -> None:
        if bool(getattr(self, "_ac_loading_settings", False)) or self._is_ac_refreshing_psu_ports():
            return
        self.ac_settings.setValue("frequencies", self.lineEdit_lcr_frequencies.text())
        self.ac_settings.setValue("levels", self.lineEdit_lcr_levels.text())
        self.ac_settings.setValue("level_mode", self.comboBox_lcr_level_mode.currentData())
        self.ac_settings.setValue("function", "Ls-Rs")
        self.ac_settings.setValue("models", ",".join(self._selected_lcr_models()))
        self.ac_settings.setValue("monitor1", self.comboBox_lcr_monitor1.currentText())
        self.ac_settings.setValue("monitor2", self.comboBox_lcr_monitor2.currentText())
        self.ac_settings.setValue("aperture", self.comboBox_lcr_aperture.currentText())
        self.ac_settings.setValue("plan_loops", int(self.checkBox_ac_plan_loops.isChecked()))
        self._capture_ac_psu_controls()
        self.ac_settings.setValue("psu_backend", self._ac_psu_backend)
        self.ac_settings.setValue("psu_port", self._ac_psu_resource)
        self.ac_settings.setValue("psu_baud", str(self._ac_psu_baudrate))
        self.ac_settings.setValue("voltage_limit_v", float(self.spinBox_ac_voltage_limit.value()))
        self._store_ac_psu_profile_settings(self._ac_psu_backend)
        self.ac_settings.setValue("current_start_mA", float(self.spinBox_ac_current_start.value()))
        self.ac_settings.setValue("current_stop_mA", float(self.spinBox_ac_current_stop.value()))
        self.ac_settings.setValue("current_step_mA", float(self.spinBox_ac_current_step.value()))
        self.ac_settings.setValue("direction", self.comboBox_ac_direction.currentData())
        self.ac_settings.setValue("dwell_s", float(self.spinBox_ac_dwell.value()))
        self.ac_settings.setValue("point_duration_s", float(self.spinBox_ac_point_duration.value()))
        self.ac_settings.setValue("include_zero_current", int(self.checkBox_ac_include_zero_current.isChecked()))

    def populate_lcr_ports(self) -> None:
        self.comboBox_lcr_port.clear()
        ports = available_serial_ports()
        for port in ports:
            self.comboBox_lcr_port.addItem(port.label, port.device)
        if not ports:
            self.comboBox_lcr_port.addItem("No serial ports found", "")
        for idx, port in enumerate(ports):
            if port.is_lcr6000:
                self.comboBox_lcr_port.setCurrentIndex(idx)
                break

    def populate_ac_psu_ports(self) -> None:
        saved_backend = str(getattr(self, "_ac_psu_backend", "") or "")
        if saved_backend:
            self._load_ac_psu_profile_settings(saved_backend)
        self._ac_refreshing_psu_ports = True
        try:
            try:
                self.populate_ports()
            except Exception:
                pass
        finally:
            self._ac_refreshing_psu_ports = False
        if saved_backend:
            self._load_ac_psu_profile_settings(saved_backend)
        self._apply_ac_psu_controls()
        self._sync_ac_psu_from_shared_controls()

    def auto_detect_power_supply(self) -> list[sweep.PowerSupplyCandidate]:
        self._auto_detect_used_connected_psu = False
        backend = self._selected_ac_psu_backend()
        resource = self._selected_ac_psu_resource()
        if bool(getattr(self, "is_connected", False)) and resource and backend in sweep.POWER_SUPPLY_PROFILES:
            self._auto_detect_used_connected_psu = True
            self._sync_ac_psu_from_shared_controls()
            profile = sweep.POWER_SUPPLY_PROFILES[backend]
            self.label_lcr_status.setText(
                f"Using connected AC {profile['label']} on {resource}; skipped ID probe because the port is already open."
            )
            self._store_lcr_settings()
            return []
        candidates = sweep.detect_power_supply_candidates()
        if not candidates:
            self._sync_ac_psu_from_shared_controls()
            return []
        current_resource = self._selected_ac_psu_resource()
        port_combo = getattr(self.ui, "comboBox_port", None)
        if isinstance(port_combo, QtWidgets.QComboBox):
            port_combo.clear()
            for candidate in candidates:
                port_combo.addItem(candidate.label, candidate.resource)
        selected_index = 0
        for idx, candidate in enumerate(candidates):
            if candidate.resource == current_resource:
                selected_index = idx
                break
        candidate = candidates[selected_index]
        if isinstance(port_combo, QtWidgets.QComboBox):
            port_combo.setCurrentIndex(selected_index)
        supply_combo = getattr(self.ui, "comboBox_supply", None)
        if isinstance(supply_combo, QtWidgets.QComboBox):
            self._set_combo_data(supply_combo, candidate.backend_id)
        baud_combo = getattr(self.ui, "comboBox_baudrate", None)
        if isinstance(baud_combo, QtWidgets.QComboBox):
            self._set_combo_text(baud_combo, str(candidate.baudrate))
        self._sync_ac_psu_from_shared_controls()
        self.label_lcr_status.setText(f"Detected PSU: {candidate.idn} on {candidate.resource}")
        self._store_lcr_settings()
        return candidates

    def handle_auto_setup_clicked(self) -> None:
        self.populate_lcr_ports()
        candidates = self.auto_detect_power_supply()
        lcr_connected = False
        if self.lcr_meter is None or not self.lcr_meter.is_open:
            port = str(self.comboBox_lcr_port.currentData() or "").strip()
            if port:
                try:
                    self.lcr_meter = Lcr6000Serial(port, baudrate=DEFAULT_BAUDRATE)
                    idn = self.lcr_meter.identify()
                    self.pushButton_connect_lcr.setText("Disconnect LCR")
                    self.label_lcr_status.setText(f"Connected: {idn or port}")
                    self._configure_lcr_for_current_index()
                    lcr_connected = True
                except Exception as exc:
                    self.lcr_meter = None
                    self.label_lcr_status.setText(f"LCR connection failed: {exc}")
        else:
            lcr_connected = True
        if not candidates:
            backend = self._selected_ac_psu_backend()
            resource = self._selected_ac_psu_resource()
            if bool(getattr(self, "_auto_detect_used_connected_psu", False)) and resource and backend in sweep.POWER_SUPPLY_PROFILES:
                self.label_lcr_status.setText(
                    f"Using connected AC {sweep.POWER_SUPPLY_PROFILES[backend]['label']} on {resource}; "
                    "skipped ID probe because the port is already open."
                )
            elif resource and backend in sweep.POWER_SUPPLY_PROFILES:
                self._sync_ac_psu_from_shared_controls()
                self.label_lcr_status.setText(
                    "Auto-detect could not read a PSU ID, but kept the manually selected "
                    f"{sweep.POWER_SUPPLY_PROFILES[backend]['label']} on {resource}."
                )
            else:
                tried = ", ".join(device for _label, device in sweep.available_power_supply_ports()) or "no serial ports"
                self.label_lcr_status.setText(
                    f"Auto-detect did not find a supported HMP/OWON power supply. Tried {tried}; select the PSU manually above if needed."
                )
        else:
            self.label_lcr_status.setText(
                f"Auto-detect selected {candidates[0].label} and LCR-6200-safe sweep defaults."
            )
        self.apply_all_lcr_frequencies()
        self.apply_all_lcr_levels()
        self.checkBox_lcr_model_lsrs.setChecked(True)
        self.checkBox_lcr_model_lprp.setChecked(False)
        self._store_lcr_settings()
        self._update_ac_sweep_estimate()
        self._refresh_ac_psu_status()
        self._refresh_ac_hardware_status(lcr_connected=lcr_connected)

    def handle_connect_lcr_clicked(self) -> None:
        if self.lcr_meter is not None and self.lcr_meter.is_open:
            self.lcr_meter.close()
            self.lcr_meter = None
            self.pushButton_connect_lcr.setText("Connect LCR")
            self.label_lcr_status.setText("LCR disconnected")
            self._refresh_ac_hardware_status()
            return
        port = str(self.comboBox_lcr_port.currentData() or "").strip()
        if not port:
            QtWidgets.QMessageBox.warning(self, "No LCR port", "Select the LCR-6200 serial port first.")
            return
        try:
            self.lcr_meter = Lcr6000Serial(port, baudrate=DEFAULT_BAUDRATE)
            idn = self.lcr_meter.identify()
        except Exception as exc:
            self.lcr_meter = None
            QtWidgets.QMessageBox.warning(self, "LCR connection failed", str(exc))
            self.label_lcr_status.setText(f"LCR connection failed: {exc}")
            return
        self.pushButton_connect_lcr.setText("Disconnect LCR")
        self.label_lcr_status.setText(f"Connected: {idn or port}")
        self._configure_lcr_for_current_index()
        self._refresh_ac_hardware_status()

    def handle_identify_lcr_clicked(self) -> None:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            QtWidgets.QMessageBox.information(self, "LCR not connected", "Connect the LCR port first.")
            return
        try:
            idn = meter.identify()
        except Exception as exc:
            self.label_lcr_status.setText(f"Identify failed: {exc}")
            return
        self.label_lcr_status.setText(f"Connected: {idn}")

    def handle_apply_lcr_setting_clicked(self) -> None:
        self._prepare_lcr_plan()
        self._lcr_plan_index = 0
        self._configure_lcr_for_current_index(show_errors=True)

    def handle_run_ac_sweep_clicked(self) -> None:
        if self._ac_sweep_running:
            self.handle_stop_ac_sweep_clicked()
            return
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            QtWidgets.QMessageBox.information(self, "LCR not connected", "Connect the LCR port first.")
            return
        try:
            config = self._build_ac_sweep_config()
            self._release_inherited_psu_port_for_ac(config.psu_resource)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid AC sweep", str(exc))
            return
        psu = sweep.SerialScpiCurrentSource(
            backend_id=config.psu_backend,
            resource=config.psu_resource,
            baudrate=self._selected_ac_psu_baudrate(),
            voltage_limit_v=config.voltage_limit_v,
        )
        output_path = self._sweep_output_path()
        self._reset_ac_live_plots("microwire_sweep_start")
        self._ac_sweep_running = True
        self._ac_sweep_stop_requested = False
        self._ac_active_sweep_config = config
        self._reset_ac_progress("Microwire sweep", self._sweep_total_reads(config), units="time")
        self._set_ac_current_task("Current task: preparing microwire current sweep")
        self.pushButton_run_ac_sweep.setEnabled(False)
        self.pushButton_stop_ac_sweep.setEnabled(True)
        self._set_sticky_action_state(running=True)
        self.label_lcr_status.setText(f"Running AC sweep: {output_path}")
        try:
            worker = AcSweepWorker(
                config=config,
                lcr=meter,
                psu=psu,
                output_path=output_path,
            )
            thread = QtCore.QThread(self)
            worker.moveToThread(thread)
            worker.row_ready.connect(self._handle_ac_sweep_progress)
            worker.finished.connect(self._handle_sweep_worker_finished)
            worker.failed.connect(self._handle_ac_worker_failed)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            thread.started.connect(worker.run)
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._clear_ac_worker_refs)
            self._ac_worker = worker
            self._ac_worker_thread = thread
            thread.start()
            return
        except Exception as exc:
            self._lcr_last_error = str(exc)
            self.label_lcr_status.setText(f"AC sweep failed: {exc}")
            QtWidgets.QMessageBox.warning(self, "AC sweep failed", str(exc))
            return

    def handle_stop_ac_sweep_clicked(self) -> None:
        if self._ac_sweep_running:
            self._ac_sweep_stop_requested = True
            worker = getattr(self, "_ac_worker", None)
            if hasattr(worker, "request_stop"):
                worker.request_stop()
            self.label_lcr_status.setText("Stopping after the current LCR read...")
            self._set_ac_current_task("Current task: stopping after current read")
            QtWidgets.QApplication.processEvents()

    def _ac_sweep_sleep(self, seconds: float) -> None:
        if not self._sleep_with_stop_processing(seconds):
            raise RuntimeError("AC sweep stopped by user")

    def _sleep_with_stop_processing(self, seconds: float, *, quantum_s: float = 0.1) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            if self._stop_requested():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(max(0.01, float(quantum_s)), remaining))
        QtWidgets.QApplication.processEvents()
        return not self._stop_requested()

    def handle_measure_lcr_baseline_clicked(self) -> None:
        if self._ac_sweep_running:
            self.handle_stop_ac_sweep_clicked()
            return
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            QtWidgets.QMessageBox.information(self, "LCR not connected", "Connect the LCR port first.")
            return
        try:
            plan = self._prepare_lcr_plan()
            point_duration = max(0.1, float(self.spinBox_ac_point_duration.value()))
            dwell = max(0.0, float(self.spinBox_ac_dwell.value()))
            baseline_seconds = len(plan) * (point_duration + dwell)
            path = self._baseline_output_path()
            self._reset_ac_live_plots("empty_coil_baseline_start")
            self._ac_sweep_running = True
            self._ac_sweep_stop_requested = False
            self._reset_ac_progress(
                "Empty-coil baseline",
                max(1, int(round(baseline_seconds * 1000.0))),
                units="time",
            )
            self._set_ac_current_task("Current task: preparing empty-coil baseline")
            self.pushButton_measure_lcr_baseline.setEnabled(False)
            self.pushButton_stop_ac_sweep.setEnabled(True)
            self._set_sticky_action_state(running=True)
            self.label_lcr_status.setText(
                f"Measuring baseline: {len(plan)} settings x {point_duration:g} s"
            )
            worker = AcBaselineWorker(
                meter=meter,
                plan=plan,
                output_path=path,
                point_duration_s=point_duration,
                settle_s=dwell,
                total_planned_s=baseline_seconds,
            )
            thread = QtCore.QThread(self)
            worker.moveToThread(thread)
            worker.task_changed.connect(self._set_ac_current_task)
            worker.progress_changed.connect(
                lambda elapsed, total: self._set_ac_elapsed_progress("Empty-coil baseline", elapsed, total)
            )
            worker.plot_point_ready.connect(self._append_ac_plot_point)
            worker.finished.connect(self._handle_baseline_worker_finished)
            worker.failed.connect(self._handle_ac_worker_failed)
            worker.finished.connect(thread.quit)
            worker.failed.connect(thread.quit)
            thread.started.connect(worker.run)
            thread.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._clear_ac_worker_refs)
            self._ac_worker = worker
            self._ac_worker_thread = thread
            thread.start()
            return
        except Exception as exc:
            self._lcr_last_error = str(exc)
            self.label_lcr_status.setText(f"Baseline failed: {exc}")
            QtWidgets.QMessageBox.warning(self, "Baseline failed", str(exc))
            return

    def _sweep_total_reads(self, config: sweep.AcSweepConfig) -> int:
        estimate = sweep.estimate_sweep(
            lcr_settings=config.lcr_settings,
            current_points=config.current_points,
            point_duration_s=config.point_duration_s,
            dwell_s=config.dwell_s,
        )
        return max(1, int(round(estimate.estimated_seconds * 1000.0)))

    def _finish_ac_worker_state(self) -> None:
        self._ac_sweep_running = False
        self._ac_sweep_stop_requested = False
        self._ac_active_sweep_config = None
        self.pushButton_measure_lcr_baseline.setEnabled(True)
        self.pushButton_run_ac_sweep.setEnabled(True)
        self.pushButton_stop_ac_sweep.setEnabled(False)
        self._set_sticky_action_state(running=False)
        self._refresh_dirty_ac_plots()

    def _clear_ac_worker_refs(self) -> None:
        self._ac_worker = None
        self._ac_worker_thread = None

    @QtCore.pyqtSlot(str, bool)
    def _handle_baseline_worker_finished(self, path_text: str, stopped: bool) -> None:
        self._finish_ac_worker_state()
        path = Path(path_text)
        if stopped:
            self.label_lcr_status.setText(f"Baseline stopped; partial file saved: {path}")
            self._set_ac_current_task("Current task: stopped")
            self.progress_ac_run.setFormat(
                f"Empty-coil baseline: stopped ({self._format_duration(self._ac_progress_value / 1000.0)} / "
                f"{self._format_duration(self._ac_progress_total / 1000.0)})"
            )
            QtWidgets.QMessageBox.information(self, "Baseline stopped", f"Saved partial LCR baseline to:\n{path}")
            return
        self.label_lcr_status.setText(f"Baseline saved: {path}")
        self._complete_ac_progress("Empty-coil baseline")
        self._set_ac_current_task("Current task: baseline complete")
        QtWidgets.QMessageBox.information(self, "Baseline saved", f"Saved LCR baseline to:\n{path}")

    @QtCore.pyqtSlot(str, bool)
    def _handle_sweep_worker_finished(self, path_text: str, stopped: bool = False) -> None:
        self._finish_ac_worker_state()
        path = Path(path_text)
        if stopped:
            self.label_lcr_status.setText(f"AC sweep stopped; partial file saved: {path}")
            self._set_ac_current_task("Current task: stopped")
            QtWidgets.QMessageBox.information(self, "AC sweep stopped", f"Saved partial AC sweep to:\n{path}")
            return
        self.label_lcr_status.setText(f"AC sweep saved: {path}")
        self._complete_ac_progress("Microwire sweep")
        self._set_ac_current_task("Current task: microwire sweep complete")
        QtWidgets.QMessageBox.information(self, "AC sweep saved", f"Saved AC sweep to:\n{path}")

    @QtCore.pyqtSlot(str)
    def _handle_ac_worker_failed(self, message: str) -> None:
        stopped = "stopped by user" in message.lower()
        self._finish_ac_worker_state()
        if stopped:
            self.label_lcr_status.setText("AC run stopped by user.")
            self._set_ac_current_task("Current task: stopped")
            return
        self._lcr_last_error = message
        self.label_lcr_status.setText(f"AC run failed: {message}")
        self._set_ac_current_task("Current task: failed")
        QtWidgets.QMessageBox.warning(self, "AC run failed", message)

    def _reset_ac_progress(self, label: str, total: int, *, units: str = "count") -> None:
        self._ac_progress_total = max(1, int(total))
        self._ac_progress_value = 0
        self._ac_progress_started_monotonic = time.monotonic()
        self._ac_progress_units = units
        progress = getattr(self, "progress_ac_run", None)
        if isinstance(progress, QtWidgets.QProgressBar):
            progress.setRange(0, self._ac_progress_total)
            progress.setValue(0)
            if units == "time":
                progress.setFormat(
                    f"{label}: 0% (0s / {self._format_duration(self._ac_progress_total / 1000.0)}), ETA calculating"
                )
            else:
                progress.setFormat(f"{label}: 0% (0/{self._ac_progress_total}), ETA calculating")
        QtWidgets.QApplication.processEvents()

    def _advance_ac_progress(self, label: str) -> None:
        try:
            current_value = int(getattr(self, "_ac_progress_value"))
            current_total = int(getattr(self, "_ac_progress_total"))
        except (AttributeError, RuntimeError):
            return
        self._ac_progress_value = min(current_value + 1, max(1, current_total))
        total = max(1, self._ac_progress_total)
        percent = int(round((self._ac_progress_value / total) * 100.0))
        progress = getattr(self, "progress_ac_run", None)
        if isinstance(progress, QtWidgets.QProgressBar):
            progress.setRange(0, total)
            progress.setValue(self._ac_progress_value)
            progress.setFormat(self._progress_format(label, percent, self._ac_progress_value, total))

    def _progress_format(self, label: str, percent: int, value: int, total: int) -> str:
        try:
            started = float(getattr(self, "_ac_progress_started_monotonic"))
        except (AttributeError, RuntimeError):
            started = 0.0
        elapsed = max(0.0, time.monotonic() - started) if started > 0 else 0.0
        if value <= 0 or elapsed <= 0:
            eta = "calculating"
            finish = ""
        else:
            remaining = max(0, total - value)
            eta_s = (elapsed / value) * remaining
            eta = self._format_duration(eta_s)
            finish = f", finish {self._format_expected_finish(eta_s)}" if eta_s > 0.0 else ""
        return f"{label}: {percent}% ({value}/{total}), ETA {eta}{finish}"

    def _complete_ac_progress(self, label: str) -> None:
        total = max(1, self._ac_progress_total)
        self._ac_progress_value = total
        progress = getattr(self, "progress_ac_run", None)
        if isinstance(progress, QtWidgets.QProgressBar):
            progress.setRange(0, total)
            progress.setValue(total)
            if getattr(self, "_ac_progress_units", "count") == "time":
                duration = self._format_duration(total / 1000.0)
                progress.setFormat(f"{label}: complete ({duration} / {duration})")
            else:
                progress.setFormat(f"{label}: complete ({total}/{total})")

    def _set_ac_elapsed_progress(self, label: str, elapsed_s: float, total_s: float) -> None:
        total_ms = max(1, int(round(max(0.001, total_s) * 1000.0)))
        value_ms = min(total_ms, max(0, int(round(max(0.0, elapsed_s) * 1000.0))))
        self._ac_progress_total = total_ms
        self._ac_progress_value = value_ms
        self._ac_progress_units = "time"
        percent = int(round((value_ms / total_ms) * 100.0))
        progress = getattr(self, "progress_ac_run", None)
        if isinstance(progress, QtWidgets.QProgressBar):
            progress.setRange(0, total_ms)
            progress.setValue(value_ms)
            eta = self._format_duration(max(0.0, total_s - elapsed_s))
            eta_s = max(0.0, total_s - elapsed_s)
            finish = f", finish {self._format_expected_finish(eta_s)}" if eta_s > 0.0 else ""
            progress.setFormat(
                f"{label}: {percent}% ({self._format_duration(elapsed_s)} / {self._format_duration(total_s)}), "
                f"ETA {eta}{finish}"
            )

    def _set_ac_planned_progress(
        self,
        label: str,
        planned_elapsed_s: float,
        total_s: float,
        *,
        wall_elapsed_s: float | None = None,
    ) -> None:
        total_ms = max(1, int(round(max(0.001, total_s) * 1000.0)))
        value_ms = min(total_ms, max(0, int(round(max(0.0, planned_elapsed_s) * 1000.0))))
        self._ac_progress_total = total_ms
        self._ac_progress_value = value_ms
        self._ac_progress_units = "time"
        percent = int(round((value_ms / total_ms) * 100.0))
        progress = getattr(self, "progress_ac_run", None)
        if isinstance(progress, QtWidgets.QProgressBar):
            progress.setRange(0, total_ms)
            progress.setValue(value_ms)
            if wall_elapsed_s is None:
                started = float(getattr(self, "_ac_progress_started_monotonic", 0.0))
                wall_elapsed_s = max(0.0, time.monotonic() - started) if started > 0.0 else max(0.0, planned_elapsed_s)
            wall_elapsed_s = max(0.0, float(wall_elapsed_s))
            planned_elapsed_s = max(0.001, planned_elapsed_s)
            remaining_planned_s = max(0.0, total_s - planned_elapsed_s)
            eta_s = (wall_elapsed_s / planned_elapsed_s) * remaining_planned_s if remaining_planned_s > 0.0 else 0.0
            finish = f", finish {self._format_expected_finish(eta_s)}" if eta_s > 0.0 else ""
            progress.setFormat(
                f"{label}: {percent}% ({self._format_duration(wall_elapsed_s)} / {self._format_duration(total_s)}), "
                f"ETA {self._format_duration(eta_s)}{finish}"
            )

    def _set_ac_progress_idle(self) -> None:
        progress = getattr(self, "progress_ac_run", None)
        if isinstance(progress, QtWidgets.QProgressBar):
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setFormat("AC progress: idle")
        self._set_ac_current_task("Current task: idle")

    def _set_ac_current_task(self, text: str) -> None:
        try:
            self._ac_last_task_text = text
        except RuntimeError:
            return
        try:
            label = getattr(self, "label_ac_current_task", None)
        except RuntimeError:
            label = None
        if isinstance(label, QtWidgets.QLabel):
            label.setText(text)
        try:
            progress_value = getattr(self, "_ac_progress_value", 0)
            progress_total = getattr(self, "_ac_progress_total", 0)
        except RuntimeError:
            progress_value = 0
            progress_total = 0
        self._write_ac_diagnostic(
            "task",
            text=text,
            progress_value=progress_value,
            progress_total=progress_total,
        )

    def _stop_requested(self) -> bool:
        try:
            return bool(getattr(self, "_ac_sweep_stop_requested"))
        except (AttributeError, RuntimeError):
            return False

    def _set_sticky_action_state(self, *, running: bool) -> None:
        start = getattr(self.ui, "pushButton_start_process", None)
        sweep_button = getattr(self.ui, "pushButton_show_history", None)
        stop = getattr(self.ui, "pushButton_reverse_now", None)
        if isinstance(start, QtWidgets.QPushButton):
            start.setEnabled(not running)
        if isinstance(sweep_button, QtWidgets.QPushButton):
            sweep_button.setEnabled(not running)
        if isinstance(stop, QtWidgets.QPushButton):
            stop.setEnabled(running)

    def _prepare_lcr_plan(self) -> list[Lcr6000Settings]:
        self._store_lcr_settings()
        level_mode = str(self.comboBox_lcr_level_mode.currentData() or "voltage")
        quantity = "current" if level_mode == "current" else "generic"
        frequencies = parse_numeric_list(self.lineEdit_lcr_frequencies.text(), quantity="frequency")
        levels = parse_numeric_list(self.lineEdit_lcr_levels.text(), quantity=quantity)
        self._lcr_plan = sweep.build_ac_settings_plan(
            models=self._selected_lcr_models(),
            frequencies_hz=frequencies,
            levels=levels,
            level_mode=level_mode,
            monitor1=self.comboBox_lcr_monitor1.currentText(),
            monitor2=self.comboBox_lcr_monitor2.currentText(),
            aperture=self.comboBox_lcr_aperture.currentText(),
        )
        if not self._lcr_plan:
            raise ValueError("No LCR settings were generated.")
        return self._lcr_plan

    def _selected_lcr_models(self) -> list[str]:
        models: list[str] = []
        models.append("Ls-Rs")
        if getattr(self, "checkBox_lcr_model_lprp", None) is not None and self.checkBox_lcr_model_lprp.isChecked():
            models.append("Lp-Rp")
        return models

    def _build_ac_sweep_config(self) -> sweep.AcSweepConfig:
        plan = self._prepare_lcr_plan()
        current_points = sweep.build_current_loop_points(
            start_mA=float(self.spinBox_ac_current_start.value()),
            stop_mA=float(self.spinBox_ac_current_stop.value()),
            step_mA=float(self.spinBox_ac_current_step.value()),
            direction_mode=str(self.comboBox_ac_direction.currentData() or "up-down"),
            include_zero=self._include_zero_current_selected(),
        )
        self._sync_ac_psu_from_shared_controls()
        psu_resource = self._selected_ac_psu_resource()
        if not psu_resource:
            raise ValueError("Select the power-supply serial port first.")
        backend = self._selected_ac_psu_backend()
        voltage_limit_v = sweep.effective_power_supply_voltage_limit(
            backend,
            float(self.spinBox_ac_voltage_limit.value()),
        )
        if voltage_limit_v != float(self.spinBox_ac_voltage_limit.value()):
            self.spinBox_ac_voltage_limit.setValue(voltage_limit_v)
        return sweep.AcSweepConfig(
            lcr_settings=plan,
            current_points=current_points,
            dwell_s=max(0.0, float(self.spinBox_ac_dwell.value())),
            psu_backend=backend,
            psu_resource=psu_resource,
            voltage_limit_v=voltage_limit_v,
            point_duration_s=max(0.1, float(self.spinBox_ac_point_duration.value())),
        )

    def _update_ac_sweep_estimate(self) -> None:
        try:
            plan = self._prepare_lcr_plan()
            current_points = sweep.build_current_loop_points(
                start_mA=float(self.spinBox_ac_current_start.value()),
                stop_mA=float(self.spinBox_ac_current_stop.value()),
                step_mA=float(self.spinBox_ac_current_step.value()),
                direction_mode=str(self.comboBox_ac_direction.currentData() or "up-down"),
                include_zero=self._include_zero_current_selected(),
            )
            estimate = sweep.estimate_sweep(
                lcr_settings=plan,
                current_points=current_points,
                point_duration_s=max(0.1, float(self.spinBox_ac_point_duration.value())),
                dwell_s=max(0.0, float(self.spinBox_ac_dwell.value())),
            )
        except Exception as exc:
            self.label_ac_sweep_estimate.setText(f"Estimate unavailable: {exc}")
            return
        point_duration = max(0.1, float(self.spinBox_ac_point_duration.value()))
        baseline_seconds = (
            len(plan) * max(0.0, float(self.spinBox_ac_dwell.value()))
            + len(plan) * point_duration
        )
        self.label_ac_sweep_estimate.setText(
            f"Baseline: about {self._format_duration(baseline_seconds)} "
            f"(finish {self._format_expected_finish(baseline_seconds)} if started now). "
            f"Microwire sweep: about "
            f"{self._format_duration(estimate.estimated_seconds)} "
            f"(finish {self._format_expected_finish(estimate.estimated_seconds)} if started now) "
            f"plus communication overhead"
        )
        self._refresh_ac_psu_status()

    def _handle_ac_sweep_progress(self, row: sweep.AcSweepRow) -> None:
        planned_elapsed_s = self._planned_ac_sweep_elapsed(row)
        total_s = max(0.001, self._ac_progress_total / 1000.0)
        if planned_elapsed_s is None:
            self._set_ac_elapsed_progress("Microwire sweep", row.elapsed_s, total_s)
        else:
            self._set_ac_planned_progress("Microwire sweep", planned_elapsed_s, total_s, wall_elapsed_s=row.elapsed_s)
        if row.error and row.repeat_index == 0:
            self._set_ac_current_task(f"Current task: microwire sweep - {row.error}")
            self.label_lcr_status.setText(row.error)
            QtWidgets.QApplication.processEvents()
            return
        self._append_ac_plot_point(self._plot_point_from_sweep_row(row))
        self._set_ac_current_task(
            "Current task: microwire sweep - "
            f"{row.setting.function}, {row.setting.frequency_hz:g} Hz, "
            f"{row.setting.level_value:g} {row.setting.level_mode}, "
            f"{row.current_point.current_a * 1000:g} mA {row.current_point.direction}, "
            f"read {row.repeat_index}"
        )
        self.label_lcr_status.setText(
            f"AC sweep {row.setting_index}/{row.total_settings}: "
            f"{row.setting.function}, {row.setting.frequency_hz:g} Hz, "
            f"{row.current_point.current_a * 1000:g} mA {row.current_point.direction}, "
            f"repeat {row.repeat_index}"
        )
        QtWidgets.QApplication.processEvents()

    def _planned_ac_sweep_elapsed(self, row: sweep.AcSweepRow) -> float | None:
        config = getattr(self, "_ac_active_sweep_config", None)
        if config is None:
            return None
        total_current_points = max(1, int(row.total_current_points))
        point_span_s = max(0.0, float(config.dwell_s)) + max(0.0, float(config.point_duration_s))
        if point_span_s <= 0.0:
            return None
        completed_current_points = max(0, int(row.current_point_index) - 1)
        completed_settings = max(0, int(row.setting_index) - 1)
        completed_points = completed_settings * total_current_points + completed_current_points
        point_progress_s = max(0.0, float(config.dwell_s)) + min(
            max(0.0, float(row.point_elapsed_s)),
            max(0.0, float(config.point_duration_s)),
        )
        return completed_points * point_span_s + point_progress_s

    def _plot_point_from_sweep_row(self, row: sweep.AcSweepRow) -> AcPlotPoint:
        ls_h, rs_ohm = self._lcr_ls_rs_values(row.setting, row.lcr_reading)
        return AcPlotPoint(
            elapsed_s=float(row.elapsed_s),
            model=row.setting.function,
            frequency_hz=float(row.setting.frequency_hz),
            amplitude_v=float(row.setting.level_value if row.setting.level_mode == "voltage" else math.nan),
            current_mA=float(row.current_point.current_a) * 1000.0,
            ls_h=ls_h,
            rs_ohm=rs_ohm,
            current_actual_mA=(
                float(row.psu_measurement.current_actual_a) * 1000.0
                if row.psu_measurement.current_actual_a is not None
                else None
            ),
            wire_resistance_ohm=row.psu_measurement.resistance_ohm,
            psu_power_w=row.psu_measurement.power_w,
        )

    def _plot_point_from_baseline_reading(
        self,
        setting: Lcr6000Settings,
        reading: Lcr6000Reading,
        *,
        elapsed_s: float,
    ) -> AcPlotPoint:
        ls_h, rs_ohm = self._lcr_ls_rs_values(setting, reading)
        return AcPlotPoint(
            elapsed_s=float(elapsed_s),
            model=setting.function,
            frequency_hz=float(setting.frequency_hz),
            amplitude_v=float(setting.level_value if setting.level_mode == "voltage" else math.nan),
            current_mA=0.0,
            ls_h=ls_h,
            rs_ohm=rs_ohm,
            current_actual_mA=0.0,
        )

    def _append_ac_plot_point(self, point: AcPlotPoint) -> None:
        try:
            points = getattr(self, "_ac_plot_points")
        except (AttributeError, RuntimeError):
            return
        points.append(point)
        if len(points) > 5000:
            del points[:-5000]
        self._ac_plot_dirty = True

    def _reset_ac_live_plots(self, reason: str) -> None:
        try:
            points = getattr(self, "_ac_plot_points")
        except (AttributeError, RuntimeError):
            return
        points.clear()
        self._ac_plot_dirty = False
        self._write_ac_diagnostic("plot_reset", reason=reason)
        self._refresh_ac_plots(force=True)

    @staticmethod
    def _lcr_ls_rs_values(setting: Lcr6000Settings, reading: Lcr6000Reading) -> tuple[float | None, float | None]:
        function = setting.function.lower()
        if "ls" in function or "lp" in function:
            return reading.primary, reading.secondary
        return reading.primary, reading.secondary

    def _sweep_output_path(self) -> Path:
        log_path = Path(self.build_log_path())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = log_path.stem.strip() or "ac_susc_current_sweep"
        if stem in {"anneal_log", "ac_susceptibility_log"}:
            stem = "ac_susc_current_sweep"
        return log_path.with_name(f"{stem}_{timestamp}.tsv")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds_int = max(0, int(round(seconds)))
        hours, remainder = divmod(seconds_int, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m {secs}s"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    @staticmethod
    def _format_expected_finish(seconds: float, *, now: datetime | None = None) -> str:
        start = now or datetime.now()
        finish = start + timedelta(seconds=max(0.0, float(seconds)))
        today = start.date()
        tomorrow = (start + timedelta(days=1)).date()
        if finish.date() == today:
            return f"today {finish:%H:%M}"
        if finish.date() == tomorrow:
            return f"tomorrow {finish:%H:%M}"
        return finish.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _format_numeric_list(values: Sequence[float]) -> str:
        return ", ".join(f"{value:g}" for value in values)

    def apply_default_lcr_presets(self, *, store: bool = True) -> None:
        self.lineEdit_lcr_frequencies.setText(self._format_numeric_list(DEFAULT_FREQUENCY_PRESETS_HZ))
        self.lineEdit_lcr_levels.setText(self._format_numeric_list(LCR_FRONT_PANEL_VOLTAGE_PRESETS_V))
        if store:
            self._store_lcr_settings()
            self._update_ac_sweep_estimate()

    def apply_all_lcr_frequencies(self) -> None:
        self.lineEdit_lcr_frequencies.setText(self._format_numeric_list(PRACTICAL_FREQUENCY_PRESETS_HZ))
        self._store_lcr_settings()
        self._update_ac_sweep_estimate()

    def apply_all_lcr_levels(self) -> None:
        self.lineEdit_lcr_levels.setText(self._format_numeric_list(LCR_FRONT_PANEL_VOLTAGE_PRESETS_V))
        self._store_lcr_settings()
        self._update_ac_sweep_estimate()

    def _load_ac_psu_settings(self) -> None:
        self._ac_psu_backend = str(self.ac_settings.value("psu_backend", "owon_spe6102", type=str))
        if self._ac_psu_backend not in sweep.POWER_SUPPLY_PROFILES:
            self._ac_psu_backend = "owon_spe6102"
        self._load_ac_psu_profile_settings(self._ac_psu_backend)
        self._apply_ac_psu_controls()
        self._apply_ac_psu_profile_state(self._ac_psu_backend)

    def _apply_ac_psu_controls(self) -> None:
        supply_combo = getattr(getattr(self, "ui", None), "comboBox_supply", None)
        if isinstance(supply_combo, QtWidgets.QComboBox):
            with QtCore.QSignalBlocker(supply_combo):
                self._set_combo_data(supply_combo, self._ac_psu_backend)
        port_combo = getattr(getattr(self, "ui", None), "comboBox_port", None)
        if isinstance(port_combo, QtWidgets.QComboBox) and self._ac_psu_resource:
            with QtCore.QSignalBlocker(port_combo):
                idx = port_combo.findData(self._ac_psu_resource)
                if idx < 0:
                    idx = port_combo.findText(self._ac_psu_resource, QtCore.Qt.MatchFlag.MatchContains)
                if idx < 0:
                    port_combo.addItem(self._ac_psu_resource, self._ac_psu_resource)
                    idx = port_combo.count() - 1
                port_combo.setCurrentIndex(idx)
        baud_combo = getattr(getattr(self, "ui", None), "comboBox_baudrate", None)
        if isinstance(baud_combo, QtWidgets.QComboBox):
            with QtCore.QSignalBlocker(baud_combo):
                idx = baud_combo.findText(str(self._ac_psu_baudrate), QtCore.Qt.MatchFlag.MatchFixedString)
                if idx >= 0:
                    baud_combo.setCurrentIndex(idx)

    def _capture_ac_psu_controls(self) -> None:
        if self._is_ac_refreshing_psu_ports():
            return
        self._ac_psu_backend = self._backend_from_ac_supply_combo()
        port_combo = getattr(getattr(self, "ui", None), "comboBox_port", None)
        if isinstance(port_combo, QtWidgets.QComboBox):
            self._ac_psu_resource = sweep.normalize_serial_resource(
                str(
                port_combo.currentData(QtCore.Qt.ItemDataRole.UserRole) or port_combo.currentText()
                )
            )
            self.port_name = self._ac_psu_resource
        baud_combo = getattr(getattr(self, "ui", None), "comboBox_baudrate", None)
        if isinstance(baud_combo, QtWidgets.QComboBox):
            try:
                self._ac_psu_baudrate = int(baud_combo.currentText())
                self.baudrate = self._ac_psu_baudrate
            except ValueError:
                pass

    def _backend_from_ac_supply_combo(self) -> str:
        combo = getattr(getattr(self, "ui", None), "comboBox_supply", None)
        backend: str | None = None
        if isinstance(combo, QtWidgets.QComboBox):
            data = combo.currentData(QtCore.Qt.ItemDataRole.UserRole)
            text = combo.currentText()
            backend = data if isinstance(data, str) and data in sweep.POWER_SUPPLY_PROFILES else None
            if backend is None:
                backend = sweep.classify_power_supply_idn(text)
            if backend is None:
                upper = text.upper()
                if "OWON" in upper or "SPE" in upper:
                    backend = "owon_spe6102"
                elif "HMP" in upper or "HAMEG" in upper or "ROHDE" in upper:
                    backend = "hmp4030"
        if backend not in sweep.POWER_SUPPLY_PROFILES:
            backend = self._ac_psu_backend if self._ac_psu_backend in sweep.POWER_SUPPLY_PROFILES else "owon_spe6102"
        return str(backend)

    def _psu_profile_key(self, backend: str, name: str) -> str:
        return f"psu_profiles/{backend}/{name}"

    def _load_ac_psu_profile_settings(self, backend: str) -> None:
        if backend not in sweep.POWER_SUPPLY_PROFILES:
            backend = "owon_spe6102"
        default_voltage = OWON_DEFAULT_VOLTAGE_LIMIT_V if backend == "owon_spe6102" else HMP_DEFAULT_VOLTAGE_LIMIT_V
        legacy_backend = str(self.ac_settings.value("psu_backend", "", type=str) or "")
        legacy_matches_backend = legacy_backend == backend
        legacy_port = str(self.ac_settings.value("psu_port", "", type=str) or "") if legacy_matches_backend else ""
        legacy_baud = (
            str(self.ac_settings.value("psu_baud", "115200", type=str) or "115200")
            if legacy_matches_backend
            else "115200"
        )
        port = self.ac_settings.value(self._psu_profile_key(backend, "port"), legacy_port, type=str)
        baud_text = self.ac_settings.value(self._psu_profile_key(backend, "baud"), legacy_baud, type=str)
        voltage = self.ac_settings.value(
            self._psu_profile_key(backend, "voltage_limit_v"),
            self.ac_settings.value("voltage_limit_v", default_voltage) if legacy_matches_backend else default_voltage,
        )
        self._ac_psu_resource = sweep.normalize_serial_resource(str(port or ""))
        try:
            self._ac_psu_baudrate = int(str(baud_text))
        except ValueError:
            self._ac_psu_baudrate = 115200
        with QtCore.QSignalBlocker(self.spinBox_ac_voltage_limit):
            try:
                self.spinBox_ac_voltage_limit.setValue(float(voltage))
            except (TypeError, ValueError):
                self.spinBox_ac_voltage_limit.setValue(default_voltage)

    def _store_ac_psu_profile_settings(self, backend: str | None = None) -> None:
        backend = backend or self._ac_psu_backend
        if backend not in sweep.POWER_SUPPLY_PROFILES:
            return
        self.ac_settings.setValue(self._psu_profile_key(backend, "port"), self._ac_psu_resource)
        self.ac_settings.setValue(self._psu_profile_key(backend, "baud"), str(self._ac_psu_baudrate))
        self.ac_settings.setValue(
            self._psu_profile_key(backend, "voltage_limit_v"),
            float(self.spinBox_ac_voltage_limit.value()),
        )

    def _apply_ac_psu_profile_state(self, backend: str) -> None:
        profile = SUPPLY_PROFILES.get(backend, SUPPLY_PROFILES.get("hmp4030", {}))
        self.supply_profile_id = backend
        self.min_start_current_mA = int(profile.get("min_start_current_mA", 1))
        self.voltage_first = bool(profile.get("voltage_first", False))
        self.reset_on_start = bool(profile.get("reset_on_start", backend != "owon_spe6102"))
        self.channel_select = int(profile.get("channel_select", 0 if backend == "owon_spe6102" else 3))

    def _selected_ac_psu_backend(self) -> str:
        self._capture_ac_psu_controls()
        return self._ac_psu_backend

    def _selected_ac_psu_resource(self) -> str:
        self._capture_ac_psu_controls()
        return self._ac_psu_resource

    def _selected_ac_psu_baudrate(self) -> int:
        self._capture_ac_psu_controls()
        return self._ac_psu_baudrate

    def _sync_ac_psu_from_shared_controls(self) -> None:
        if self._is_ac_refreshing_psu_ports():
            return
        self._capture_ac_psu_controls()
        backend = self._selected_ac_psu_backend()
        if backend == "owon_spe6102":
            default_limit = OWON_DEFAULT_VOLTAGE_LIMIT_V
        else:
            default_limit = HMP_DEFAULT_VOLTAGE_LIMIT_V
        current_limit = float(self.spinBox_ac_voltage_limit.value())
        if (
            not math.isfinite(current_limit)
            or current_limit <= 0
            or (
                backend == "owon_spe6102"
                and (
                    current_limit <= 5.0
                    or math.isclose(current_limit, 60.0)
                    or current_limit > OWON_DEFAULT_VOLTAGE_LIMIT_V
                )
            )
            or (backend != "owon_spe6102" and math.isclose(current_limit, OWON_DEFAULT_VOLTAGE_LIMIT_V))
        ):
            self.spinBox_ac_voltage_limit.setValue(default_limit)
        self._refresh_ac_psu_status()

    def _is_ac_refreshing_psu_ports(self) -> bool:
        try:
            return bool(getattr(self, "_ac_refreshing_psu_ports", False))
        except RuntimeError:
            return False

    def _include_zero_current_selected(self) -> bool:
        try:
            checkbox = getattr(self, "checkBox_ac_include_zero_current", None)
        except RuntimeError:
            return False
        return bool(isinstance(checkbox, QtWidgets.QCheckBox) and checkbox.isChecked())

    def _release_inherited_psu_port_for_ac(self, resource: str) -> None:
        """Close the inherited Qt serial handle before the AC worker opens pyserial."""
        if not bool(getattr(self, "is_connected", False)):
            return
        if bool(getattr(self, "process_running", False)):
            raise RuntimeError("Stop the inherited current-annealing process before starting an AC current sweep.")
        selected = sweep.normalize_serial_resource(resource)
        active = sweep.normalize_serial_resource(str(getattr(self, "port_name", "") or ""))
        if active and selected and active != selected:
            return
        try:
            self.send_safe_end_commands()
        except Exception:
            pass
        try:
            self.ser_mcu.readyRead.disconnect(self.handle_ser_mcu_readyRead)
        except Exception:
            pass
        try:
            self.ser_mcu.close()
        except Exception:
            pass
        self.is_connected = False
        try:
            self.ui.pushButton_connect_port.setText("Connect")
            self._set_port_controls_enabled(True)
            self._update_mode_action_state()
        except Exception:
            pass

    def _refresh_ac_psu_status(self) -> None:
        try:
            label = getattr(self, "label_ac_psu_status", None)
        except RuntimeError:
            return
        if not isinstance(label, QtWidgets.QLabel):
            return
        backend = str(getattr(self, "_ac_psu_backend", "") or "owon_spe6102")
        if backend not in sweep.POWER_SUPPLY_PROFILES:
            backend = "owon_spe6102"
        profile = sweep.POWER_SUPPLY_PROFILES.get(backend, {})
        backend_label = str(profile.get("label", backend))
        resource = str(getattr(self, "_ac_psu_resource", "") or "") or "no port selected"
        baudrate = int(getattr(self, "_ac_psu_baudrate", 115200) or 115200)
        label.setText(f"AC current supply: {backend_label}, {resource}, {baudrate} baud")
        self._refresh_ac_hardware_status()

    def _set_ac_hardware_details_visible(self, checked: bool) -> None:
        details = getattr(self, "frame_ac_hardware_details", None)
        if isinstance(details, QtWidgets.QWidget):
            details.setVisible(bool(checked))
        button = getattr(self, "pushButton_ac_hardware_details", None)
        if isinstance(button, QtWidgets.QToolButton):
            button.setText("Hide hardware details" if checked else "Show hardware details")

    def _refresh_ac_hardware_status(self, *, lcr_connected: bool | None = None) -> None:
        try:
            label = getattr(self, "label_ac_hardware_status", None)
        except RuntimeError:
            return
        if not isinstance(label, QtWidgets.QLabel):
            return
        if lcr_connected is None:
            lcr = getattr(self, "lcr_meter", None)
            lcr_connected = bool(lcr is not None and getattr(lcr, "is_open", False))
        backend = str(getattr(self, "_ac_psu_backend", "") or "owon_spe6102")
        if backend not in sweep.POWER_SUPPLY_PROFILES:
            backend = "owon_spe6102"
        profile = sweep.POWER_SUPPLY_PROFILES.get(backend, {})
        backend_label = str(profile.get("label", backend))
        resource = str(getattr(self, "_ac_psu_resource", "") or "")
        psu_text = f"{backend_label} on {resource}" if resource else f"{backend_label}, no port selected"
        lcr_text = "LCR connected" if lcr_connected else "LCR not connected"
        label.setText(f"{lcr_text}; PSU {psu_text}")

    def _install_ac_wheel_guard(self, control_root: QtWidgets.QWidget) -> None:
        self._ac_lcr_scroll_area = self._find_parent_scroll_area(control_root)
        for widget in control_root.findChildren((QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
            widget.setProperty("_ac_wheel_guard", True)
            widget.installEventFilter(self)
            if isinstance(widget, QtWidgets.QAbstractSpinBox):
                editor = widget.lineEdit()
                editor.setProperty("_ac_wheel_guard", True)
                editor.installEventFilter(self)

    @staticmethod
    def _find_parent_scroll_area(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea | None:
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QtWidgets.QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if (
            event.type() == QtCore.QEvent.Type.Wheel
            and isinstance(watched, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QLineEdit))
            and watched.property("_ac_wheel_guard")
        ):
            if isinstance(watched, QtWidgets.QComboBox) and watched.view().isVisible():
                return super().eventFilter(watched, event)
            self._scroll_ac_panel_from_wheel(event)
            return True
        return super().eventFilter(watched, event)

    def _scroll_ac_panel_from_wheel(self, event: QtCore.QEvent) -> None:
        if not isinstance(event, QtGui.QWheelEvent):
            event.ignore()
            return
        scroll_area = self._ac_lcr_scroll_area
        if scroll_area is None:
            event.ignore()
            return
        scrollbar = scroll_area.verticalScrollBar()
        delta = event.pixelDelta().y()
        if delta == 0:
            delta = int(event.angleDelta().y() / 120 * scrollbar.singleStep() * 3)
        if delta != 0:
            scrollbar.setValue(scrollbar.value() - delta)
        event.accept()

    def _configure_lcr_for_current_index(self, *, show_errors: bool = False) -> bool:
        if not self._lcr_plan:
            try:
                self._prepare_lcr_plan()
            except Exception as exc:
                if show_errors:
                    QtWidgets.QMessageBox.warning(self, "Invalid AC settings", str(exc))
                self.label_lcr_status.setText(f"Invalid AC settings: {exc}")
                return False
        index = min(max(0, self._lcr_plan_index), len(self._lcr_plan) - 1)
        setting = self._lcr_plan[index]
        meter = self.lcr_meter
        if meter is not None and meter.is_open:
            try:
                meter.configure(setting)
            except Exception as exc:
                self._lcr_last_error = str(exc)
                if show_errors:
                    QtWidgets.QMessageBox.warning(self, "LCR configure failed", str(exc))
                self.label_lcr_status.setText(f"LCR configure failed: {exc}")
                return False
        plan_count = len(self._lcr_plan)
        level_unit = "A" if setting.level_mode == "current" else "V"
        self.label_lcr_status.setText(
            f"AC setting {index + 1}/{plan_count}: {setting.function}, "
            f"{setting.frequency_hz:g} Hz, {setting.level_value:g} {level_unit}"
        )
        return True

    def handle_toggle_process_clicked(self):  # type: ignore[override]
        starting = not bool(getattr(self, "process_running", False))
        if starting:
            try:
                plan = self._prepare_lcr_plan()
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Invalid AC settings", str(exc))
                return
            self._lcr_plan_index = 0
            if self.checkBox_ac_plan_loops.isChecked() and len(plan) > 1:
                loops = getattr(self.ui, "spinBox_loops", None)
                infinite = getattr(self.ui, "checkBox_infinite_loops", None)
                reverse = getattr(self.ui, "checkBox_reverse", None)
                if isinstance(infinite, QtWidgets.QCheckBox):
                    infinite.setChecked(False)
                if isinstance(reverse, QtWidgets.QCheckBox):
                    reverse.setChecked(True)
                if isinstance(loops, QtWidgets.QSpinBox):
                    loops.setValue(len(plan))
            if not self._configure_lcr_for_current_index(show_errors=True):
                return
        super().handle_toggle_process_clicked()

    def _finalize_loop_cycle(self) -> None:
        super()._finalize_loop_cycle()
        if not self.checkBox_ac_plan_loops.isChecked() or not self._lcr_plan:
            return
        try:
            next_index = int(getattr(self, "loop_idx", 0))
        except Exception:
            next_index = self._lcr_plan_index + 1
        if 0 <= next_index < len(self._lcr_plan):
            self._lcr_plan_index = next_index
            self._configure_lcr_for_current_index(show_errors=False)

    def _current_lcr_setting(self) -> Lcr6000Settings | None:
        if not self._lcr_plan:
            try:
                self._prepare_lcr_plan()
            except Exception:
                return None
        if not self._lcr_plan:
            return None
        index = min(max(0, self._lcr_plan_index), len(self._lcr_plan) - 1)
        return self._lcr_plan[index]

    def _fetch_lcr_reading(self) -> Lcr6000Reading | None:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            return None
        try:
            reading = meter.fetch_impedance()
        except Exception as exc:
            self._lcr_last_error = str(exc)
            self.label_lcr_status.setText(f"LCR read failed: {exc}")
            return None
        self._lcr_last_reading = reading
        return reading

    def _baseline_output_path(self) -> Path:
        log_path = Path(self.build_log_path())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return log_path.with_name(f"ac_susc_empty_coil_baseline_{timestamp}.tsv")

    def _collect_baseline_rows(
        self,
        plan: Sequence[Lcr6000Settings],
        *,
        point_duration_s: float | None = None,
        repeats: int | None = None,
        settle_s: float = 0.0,
        total_planned_s: float | None = None,
        row_callback: Callable[[list[str]], None] | None = None,
    ) -> list[list[str]]:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            raise RuntimeError("LCR meter is not connected")
        rows: list[list[str]] = []
        if point_duration_s is None:
            point_duration_s = 0.0
        point_duration = max(0.0, float(point_duration_s))
        fallback_repeats = max(1, int(repeats if repeats is not None else 1))
        started = time.monotonic()
        for setting_index, setting in enumerate(plan, start=1):
            if self._stop_requested():
                break
            meter.configure(setting)
            settle = max(0.0, float(settle_s))
            if settle and not self._sleep_with_stop_processing(settle):
                break
            self._lcr_plan_index = setting_index - 1
            point_started = time.monotonic()
            repeat_index = 0
            while True:
                if self._stop_requested():
                    break
                repeat_index += 1
                self._set_ac_current_task(
                    "Current task: empty-coil baseline - "
                    f"{setting.function}, {setting.frequency_hz:g} Hz, "
                    f"{setting.level_value:g} {setting.level_mode}, "
                    f"{self._format_duration(time.monotonic() - point_started)} / {self._format_duration(point_duration)}"
                )
                reading = self._fetch_baseline_reading()
                self._lcr_last_reading = reading
                row = self._format_baseline_row(
                    setting_index=setting_index,
                    repeat_index=repeat_index,
                    setting=setting,
                    reading=reading,
                )
                rows.append(row)
                if row_callback is not None:
                    row_callback(row)
                if total_planned_s is not None:
                    self._set_ac_elapsed_progress("Empty-coil baseline", time.monotonic() - started, total_planned_s)
                else:
                    self._advance_ac_progress("Empty-coil baseline")
                self._append_ac_plot_point(
                    self._plot_point_from_baseline_reading(
                        setting,
                        reading,
                        elapsed_s=time.monotonic() - started,
                    )
                )
                QtWidgets.QApplication.processEvents()
                if point_duration > 0.0:
                    if time.monotonic() - point_started >= point_duration:
                        break
                elif repeat_index >= fallback_repeats:
                    break
        return rows

    def _fetch_baseline_reading(self, *, attempts: int = 3) -> Lcr6000Reading:
        meter = self.lcr_meter
        if meter is None or not meter.is_open:
            raise RuntimeError("LCR meter is not connected")
        for attempt in range(1, max(1, int(attempts)) + 1):
            reading = meter.fetch_impedance()
            if reading.raw.strip():
                return reading
            if attempt < attempts:
                self._lcr_last_error = "Empty LCR response"
                QtWidgets.QApplication.processEvents()
                time.sleep(0.25)
        raise RuntimeError("LCR returned an empty response during baseline measurement")

    @staticmethod
    def _format_baseline_row(
        *,
        setting_index: int,
        repeat_index: int,
        setting: Lcr6000Settings,
        reading: Lcr6000Reading,
    ) -> list[str]:
        return [
            reading.timestamp_utc,
            str(setting_index),
            str(repeat_index),
            CurrentAnnealingWindow._format_sample_value(setting.frequency_hz),
            setting.level_mode,
            CurrentAnnealingWindow._format_sample_value(setting.level_value),
            setting.function,
            MainWindow._format_optional_sample_value(reading.primary),
            MainWindow._format_optional_sample_value(reading.secondary),
            MainWindow._format_optional_sample_value(reading.monitor1),
            MainWindow._format_optional_sample_value(reading.monitor2),
            reading.comparator,
            reading.raw,
        ]

    @staticmethod
    def _format_optional_sample_value(value: float | None) -> str:
        if value is None or not math.isfinite(value):
            return ""
        return CurrentAnnealingWindow._format_sample_value(value)

    @staticmethod
    def _write_baseline_header(
        fh: Any,
        plan: Sequence[Lcr6000Settings],
        *,
        point_duration_s: float | None = None,
        settle_s: float | None = None,
    ) -> None:
        fh.write("# AC susceptibility baseline generated from LCR-6200 settings\n")
        fh.write(
            "# config_json="
            + MainWindow._baseline_settings_snapshot_json(
                plan,
                point_duration_s=point_duration_s,
                settle_s=settle_s,
            )
            + "\n"
        )
        for index, setting in enumerate(plan, start=1):
            commands = " ".join(command.strip() for command in commands_for_settings(setting))
            fh.write(f"# AC setting {index}: {commands}\n")
        fh.write(BASELINE_HEADER_LINE + "\n")
        fh.flush()

    @staticmethod
    def _baseline_settings_snapshot_json(
        plan: Sequence[Lcr6000Settings],
        *,
        point_duration_s: float | None = None,
        settle_s: float | None = None,
    ) -> str:
        snapshot = {
            "run_type": "empty_coil_baseline",
            "created_utc": datetime.now().astimezone().isoformat(),
            "sample": "empty_coil_no_sample",
            "acquisition": {
                "point_duration_s": None if point_duration_s is None else float(point_duration_s),
                "settle_s": None if settle_s is None else float(settle_s),
            },
            "lcr_settings": [
                {
                    "function": setting.function,
                    "frequency_hz": float(setting.frequency_hz),
                    "level_mode": setting.level_mode,
                    "level_value": float(setting.level_value),
                    "monitor1": setting.monitor1,
                    "monitor2": setting.monitor2,
                    "aperture": setting.aperture,
                }
                for setting in plan
            ],
        }
        return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _write_baseline_row(fh: Any, row: Sequence[str]) -> None:
        fh.write("\t".join(row) + "\n")
        fh.flush()

    @staticmethod
    def _write_baseline_file(
        path: str | Path,
        plan: Sequence[Lcr6000Settings],
        rows: Sequence[Sequence[str]],
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as fh:
            MainWindow._write_baseline_header(fh, plan)
            for row in rows:
                MainWindow._write_baseline_row(fh, row)

    def _ensure_log_header(self, path: str) -> None:  # type: ignore[override]
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return
        lines = text.splitlines()
        if not lines:
            lines = [HEADER_LINE]
        else:
            header_index: int | None = None
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("#") and "Current" in stripped and "Resistance" in stripped:
                    header_index = idx
                    break
            if header_index is None:
                lines.insert(0, HEADER_LINE)
            else:
                lines[header_index] = HEADER_LINE
        try:
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def prepare_output_file(self) -> bool:  # type: ignore[override]
        path = self.build_log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass

        mode = "w"
        if os.path.exists(path):
            msg = QtWidgets.QMessageBox(self)
            msg.setWindowTitle("File exists")
            msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
            base = os.path.basename(path)
            msg.setText(f"'{base}' already exists.")
            msg.setInformativeText("Choose an action:")
            replace_btn = msg.addButton("Replace", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
            continue_btn = msg.addButton("Continue", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
            cancel_btn = msg.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is cancel_btn:
                return False
            if clicked is continue_btn:
                mode = "a"
            elif clicked is replace_btn:
                mode = "w"
            else:
                return False
        try:
            if mode == "a":
                self._ensure_log_header(path)
            with open(path, mode, encoding="utf-8") as fh:
                if mode != "a":
                    fh.write(HEADER_LINE + "\n")
                    self._write_lcr_metadata(fh)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to open {path}: {exc}")
            return False

        self.f_name = path
        return True

    def _write_lcr_metadata(self, fh: Any) -> None:
        if not self._lcr_plan:
            return
        fh.write("# AC susceptibility plan generated from LCR-6200 settings\n")
        for index, setting in enumerate(self._lcr_plan, start=1):
            commands = " ".join(command.strip() for command in commands_for_settings(setting))
            fh.write(f"# AC setting {index}: {commands}\n")

    def _write_sample_to_file(self, *, initial_sample: bool) -> None:  # type: ignore[override]
        if initial_sample or not self.f_name:
            return
        current_mA = float(self.current_current_read) * 1000.0
        voltage = float(self.current_voltage)
        resistance = float(self.current_resistance)
        if not math.isfinite(current_mA) or not math.isfinite(resistance):
            return
        setting = self._current_lcr_setting()
        reading = self._fetch_lcr_reading()
        if not self.f_out:
            try:
                Path(self.f_name).parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            try:
                self.f_out = open(self.f_name, "a", encoding="utf-8")
            except OSError:
                self.f_out = None
        if self.f_out:
            extras = self._format_lcr_columns(setting, reading)
            line = "\t".join(
                [
                    self._format_sample_value(current_mA),
                    self._format_sample_value(voltage),
                    self._format_sample_value(resistance),
                    *extras,
                ]
            ) + "\n"
            self.f_out.write(line)
            self.f_out.close()
            self.f_out = None

    def _format_lcr_columns(
        self,
        setting: Lcr6000Settings | None,
        reading: Lcr6000Reading | None,
    ) -> list[str]:
        if setting is None:
            base = ["", "", "", "", ""]
        else:
            base = [
                str(self._lcr_plan_index + 1),
                self._format_sample_value(setting.frequency_hz),
                setting.level_mode,
                self._format_sample_value(setting.level_value),
                setting.function,
            ]
        if reading is None:
            return base + ["", "", "", "", "", ""]
        return base + [
            self._format_optional_float(reading.primary),
            self._format_optional_float(reading.secondary),
            self._format_optional_float(reading.monitor1),
            self._format_optional_float(reading.monitor2),
            reading.comparator,
            reading.raw,
        ]

    def _format_optional_float(self, value: float | None) -> str:
        return self._format_optional_sample_value(value)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[override]
        meter = self.lcr_meter
        if meter is not None:
            try:
                meter.close()
            except Exception:
                pass
            self.lcr_meter = None
        super().closeEvent(event)


WINDOWS: list[QtWidgets.QWidget] = []


def main() -> QtWidgets.QWidget:
    app = QtWidgets.QApplication.instance()
    owns_app = False
    if app is None:
        qt_app = QtWidgets.QApplication(sys.argv)
        owns_app = True
    else:
        qt_app = cast(QtWidgets.QApplication, app)

    ensure_app_theme(qt_app)
    _apply_app_font_to_matplotlib(qt_app)
    win = MainWindow()
    WINDOWS.append(win)
    win.show()
    if owns_app:
        qt_app.exec()
    return win


if __name__ == "__main__":
    main()
