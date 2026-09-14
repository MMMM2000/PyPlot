"""Editable current-program logger for HMP and Keithley supplies.

This experiment keeps acquisition independent from the recipe editor.  A recipe
is a sequence of ramp, pulse, and hold blocks; logging continues after the last
finite block until the operator presses Stop.
"""

from __future__ import annotations

import csv
import json
import math
import sys
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from queue import SimpleQueue, Empty
from typing import Any, Callable, Protocol, Sequence

from PyQt6 import QtCore, QtGui, QtWidgets
from experiments.siglent_spd1305x import SiglentSPD1305XAdapter
from experiments.current_program_support import BufferedCsv, ElectricalLimits, ElectricalMonitor
from experiments.current_program_profiles import PROFILES
from plotting.shared.power_guard import create_experiment_sleep_guard

from data_logging.shared_power_supply.broker import ROLE_CURRENT_ANNEALING
from data_logging.shared_power_supply.protocol import BrokerJsonClient
from plotting.shared.experiment_processes import ExperimentProcessSpec, launch_experiment_process
from plotting.shared.utils import ensure_app_theme, install_standard_menu

try:
    import pyqtgraph as pg
except Exception:  # pragma: no cover - optional plotting fallback
    pg = None  # type: ignore[assignment]


APP_TITLE = "Current Program Logger"
RESOURCE_TAG = "current_program_logger"
BLOCK_KINDS = ("Ramp", "Pulse", "Hold")
CSV_FIELDS = (
    "timestamp_utc",
    "elapsed_s",
    "cycle_index",
    "block_index",
    "block_type",
    "block_label",
    "sequence_complete",
    "target_current_mA",
    "measured_current_mA",
    "voltage_V",
    "resistance_ohm",
    "qualified_resistance_ohm",
    "resistance_quality",
    "power_mW",
    "resistance_limit_reached",
    "resistance_current_ceiling_mA",
    "operating_mode",
    "sequence_id",
    "sequence_elapsed_s",
    "electrical_fault",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CurrentBlock:
    kind: str
    target_mA: float
    duration_s: float | None
    label: str = ""
    resistance_ohm: float | None = None
    resistance_direction: str = "above"

    @property
    def indefinite(self) -> bool:
        return self.duration_s is None

    def validate(self, *, max_current_mA: float) -> None:
        if self.resistance_direction not in {"above", "below"}:
            raise ValueError("Resistance direction must be above or below.")
        if self.resistance_ohm is not None:
            if not math.isfinite(self.resistance_ohm) or self.resistance_ohm <= 0:
                raise ValueError("Resistance target must be positive and finite.")
            if self.duration_s is None:
                raise ValueError("Resistance steps require a maximum duration.")
        if self.kind not in BLOCK_KINDS:
            raise ValueError(f"Unknown block type: {self.kind}")
        if not math.isfinite(self.target_mA) or self.target_mA < 0:
            raise ValueError("Block current must be zero or greater.")
        if self.target_mA > max_current_mA:
            raise ValueError(
                f"Block current {self.target_mA:g} mA exceeds the {max_current_mA:g} mA run limit."
            )
        if self.duration_s is None:
            if self.kind != "Hold":
                raise ValueError("Only a Hold block can be indefinite.")
        elif not math.isfinite(self.duration_s) or self.duration_s <= 0:
            raise ValueError(f"{self.kind} duration must be greater than zero.")


def validate_recipe(
    blocks: Sequence[CurrentBlock],
    *,
    max_current_mA: float,
    repeat_count: int | None = 1,
) -> None:
    if not blocks:
        raise ValueError("Add at least one block before starting.")
    if repeat_count is not None and repeat_count < 1:
        raise ValueError("Repeat count must be at least one.")
    for index, block in enumerate(blocks):
        block.validate(max_current_mA=max_current_mA)
        if block.indefinite and index != len(blocks) - 1:
            raise ValueError("An indefinite Hold must be the final block.")
        if block.indefinite and repeat_count != 1:
            raise ValueError("An indefinite Hold cannot be combined with repeated cycles.")


@dataclass(frozen=True, slots=True)
class ProgramState:
    cycle_index: int
    block_index: int
    block: CurrentBlock
    target_mA: float
    block_elapsed_s: float
    sequence_complete: bool = False


class RecipeEngine:
    """Pure time-to-setpoint mapping used by both the worker and tests."""

    def __init__(
        self,
        blocks: Sequence[CurrentBlock],
        *,
        initial_current_mA: float,
        repeat_count: int | None = 1,
    ) -> None:
        self.blocks = tuple(blocks)
        self.initial_current_mA = float(initial_current_mA)
        self.repeat_count = repeat_count
        if not self.blocks:
            raise ValueError("Recipe cannot be empty.")
        if repeat_count is not None and repeat_count < 1:
            raise ValueError("Repeat count must be at least one.")
        self.cycle_duration_s = sum(
            float(block.duration_s) for block in self.blocks if block.duration_s is not None
        )
        self._indefinite = any(block.duration_s is None for block in self.blocks)
        if self._indefinite and repeat_count != 1:
            raise ValueError("An indefinite Hold cannot be repeated.")

    def state_at(self, elapsed_s: float) -> ProgramState:
        elapsed = max(0.0, float(elapsed_s))
        cycle_index = 0
        sequence_complete = False
        if not self._indefinite and self.cycle_duration_s > 0:
            if self.repeat_count is not None and elapsed >= self.cycle_duration_s * self.repeat_count:
                cycle_index = self.repeat_count - 1
                block = self.blocks[-1]
                return ProgramState(
                    cycle_index,
                    len(self.blocks) - 1,
                    block,
                    block.target_mA,
                    elapsed - self.cycle_duration_s * self.repeat_count,
                    sequence_complete=True,
                )
            cycle_index = int(elapsed // self.cycle_duration_s)
            elapsed -= cycle_index * self.cycle_duration_s
        remaining = elapsed
        previous_target = (
            self.initial_current_mA if cycle_index == 0 else self.blocks[-1].target_mA
        )
        for index, block in enumerate(self.blocks):
            if block.duration_s is None or remaining < block.duration_s:
                if block.kind == "Ramp" and block.duration_s:
                    fraction = min(1.0, remaining / block.duration_s)
                    target = previous_target + (block.target_mA - previous_target) * fraction
                else:
                    target = block.target_mA
                return ProgramState(cycle_index, index, block, target, remaining)
            remaining -= block.duration_s
            previous_target = block.target_mA
        block = self.blocks[-1]
        return ProgramState(
            cycle_index,
            len(self.blocks) - 1,
            block,
            block.target_mA,
            max(0.0, remaining),
            sequence_complete=True,
        )


class ConditionalRecipeEngine:
    """Sequential resistance-aware scheduler; timeout never advances into heating."""
    def __init__(self, blocks, *, initial_current_mA, repeat_count=1):
        self.blocks = tuple(blocks)
        self.repeat_count = repeat_count
        self.previous = initial_current_mA
        self.index = self.cycle = 0
        self.started = 0.0
        self.complete = self.timed_out = False
        self.hits = 0
        self.last_reading = None

    def _advance(self, at, current):
        self.previous = current
        self.started = at
        self.hits = 0
        self.last_reading = None
        self.index += 1
        if self.index == len(self.blocks):
            if self.repeat_count is not None and self.cycle + 1 >= self.repeat_count:
                self.index -= 1
                self.complete = True
            else:
                self.index = 0
                self.cycle += 1

    def state_at(self, elapsed):
        while not self.complete and not self.timed_out:
            block = self.blocks[self.index]
            if block.duration_s is None or elapsed < self.started + block.duration_s:
                break
            if block.resistance_ohm is not None:
                self.timed_out = True
                break
            self._advance(self.started + block.duration_s, block.target_mA)
        block = self.blocks[self.index]
        age = max(0.0, elapsed-self.started)
        value = self.previous if self.complete else block.target_mA
        if block.kind == "Ramp" and not self.complete:
            value = self.previous + (block.target_mA-self.previous)*min(1.0,age/block.duration_s)
        return ProgramState(self.cycle,self.index,block,value,age,self.complete)

    def observe(self, elapsed, resistance, expected_step=None):
        state = self.state_at(elapsed)
        if expected_step is not None and expected_step != (state.cycle_index, state.block_index):
            return False
        threshold = state.block.resistance_ohm
        if threshold is None or self.complete or self.timed_out:
            return False
        valid = resistance is not None and math.isfinite(resistance) and resistance > 0
        beyond = valid and (resistance >= threshold if state.block.resistance_direction == "above" else resistance <= threshold)
        if not beyond:
            self.hits = 0
        else:
            if self.last_reading is None or elapsed-self.last_reading > .005:
                self.hits = 0
            self.hits += 1
        self.last_reading = elapsed
        if self.hits >= 3:
            self._advance(elapsed, state.target_mA)
            return True
        return False


def make_recipe_engine(blocks, **kwargs):
    engine_type = ConditionalRecipeEngine if any(b.resistance_ohm is not None for b in blocks) else RecipeEngine
    return engine_type(blocks, **kwargs)


class SupplyAdapter(Protocol):
    description: str

    def open(self) -> None: ...
    def configure(self, *, voltage_v: float, current_mA: float) -> None: ...
    def set_current(self, current_mA: float) -> None: ...
    def measure(self) -> dict[str, float | None]: ...
    def close(self) -> None: ...


class SimulatedSupplyAdapter:
    description = "Deterministic simulation"

    def __init__(self) -> None:
        self.current_mA = 0.0
        self._started = time.monotonic()

    def open(self) -> None:
        self._started = time.monotonic()

    def configure(self, *, voltage_v: float, current_mA: float) -> None:
        del voltage_v
        self.current_mA = max(0.0, float(current_mA))

    def set_current(self, current_mA: float) -> None:
        self.current_mA = max(0.0, float(current_mA))

    def measure(self) -> dict[str, float]:
        phase = time.monotonic() - self._started
        measured = self.current_mA * (1.0 + 0.002 * math.sin(phase * 2.1))
        resistance = 100.0 + 0.8 * math.sin(phase * 0.17)
        return {"current_mA": measured, "voltage_V": measured * resistance / 1000.0}

    def close(self) -> None:
        self.current_mA = 0.0


class BrokerSupplyAdapter:
    profile_mode = "Shared HMP broker"
    description = "Shared HMP broker"

    def __init__(self, *, host: str, port: int, channel: int) -> None:
        self.client = BrokerJsonClient(host=host, port=port)
        self.channel = int(channel)
        self.owner = f"current_program_logger-{uuid.uuid4().hex[:8]}"
        self.lease_id = ""

    def open(self) -> None:
        snapshot = self.client.snapshot()
        profile = snapshot.get("profile", {})
        count = int(profile.get("channel_count", 0)) if isinstance(profile, dict) else 0
        if self.channel < 1 or (count and self.channel > count):
            raise RuntimeError(f"CH{self.channel} is not available on the connected supply.")
        lease = self.client.lease(
            channel=self.channel,
            owner=self.owner,
            role=ROLE_CURRENT_ANNEALING,
        )
        self.lease_id = str(lease.get("lease_id") or "")
        if not self.lease_id:
            raise RuntimeError("Shared HMP broker did not return a lease id.")

    def configure(self, *, voltage_v: float, current_mA: float) -> None:
        self.client.configure_channel(
            channel=self.channel,
            lease_id=self.lease_id,
            voltage_v=voltage_v,
            current_a=max(0.0, current_mA) / 1000.0,
            output_on=True,
        )

    def set_current(self, current_mA: float) -> None:
        self.client.set_current(
            channel=self.channel,
            lease_id=self.lease_id,
            current_mA=max(0.0, current_mA),
        )

    def measure(self) -> dict[str, float | None]:
        return self.client.measure_channel(channel=self.channel)

    def close(self) -> None:
        if not self.lease_id:
            return
        try:
            self.client.set_output(channel=self.channel, lease_id=self.lease_id, output_on=False)
        finally:
            try:
                self.client.release(channel=self.channel, lease_id=self.lease_id)
            finally:
                self.lease_id = ""


class VisaResource(Protocol):
    timeout: int
    write_termination: str
    read_termination: str

    def write(self, command: str) -> Any: ...
    def query(self, command: str) -> str: ...
    def close(self) -> None: ...


class VisaResourceManager(Protocol):
    def list_resources(self) -> Sequence[str]: ...
    def open_resource(self, resource_name: str) -> VisaResource: ...
    def close(self) -> None: ...


def _default_visa_resource_manager() -> VisaResourceManager:
    try:
        import pyvisa
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("PyVISA is not installed. Run 'uv sync' before using Keithley mode.") from exc
    return pyvisa.ResourceManager()


def list_visa_resources(
    resource_manager_factory: Callable[[], VisaResourceManager] = _default_visa_resource_manager,
) -> tuple[str, ...]:
    manager = resource_manager_factory()
    try:
        return tuple(str(resource) for resource in manager.list_resources())
    finally:
        manager.close()


class Keithley2636Adapter:
    profile_mode = "Keithley 2636B (VISA)"
    """PyVISA/TSP adapter for either channel of a Keithley 2636/2636B SMU."""

    def __init__(
        self,
        *,
        resource_name: str,
        channel: str,
        remote_sense: bool,
        current_limit_mA: float = 1500.0,
        resource_manager_factory: Callable[[], VisaResourceManager] = _default_visa_resource_manager,
    ) -> None:
        self.resource_name = resource_name.strip()
        self.channel = channel.strip().lower()
        self.remote_sense = bool(remote_sense)
        self.current_limit_mA = max(0.0, float(current_limit_mA))
        self._resource_manager_factory = resource_manager_factory
        self._manager: VisaResourceManager | None = None
        self._instrument: VisaResource | None = None
        self.description = f"Keithley 2636B SMU channel {self.channel.upper()}"
        if self.channel not in {"a", "b"}:
            raise ValueError("Keithley channel must be A or B.")

    @property
    def _smu(self) -> str:
        return f"smu{self.channel}"

    def _device(self) -> VisaResource:
        if self._instrument is None:
            raise RuntimeError("Keithley is not connected.")
        return self._instrument

    def open(self) -> None:
        if not self.resource_name:
            raise RuntimeError("Select or enter a Keithley VISA resource first.")
        manager = self._resource_manager_factory()
        try:
            instrument = manager.open_resource(self.resource_name)
            instrument.timeout = 5000
            instrument.write_termination = "\n"
            instrument.read_termination = "\n"
            identity = instrument.query("*IDN?").strip()
            if "KEITHLEY" not in identity.upper() or "2636" not in identity.upper():
                instrument.close()
                raise RuntimeError(f"Selected VISA resource is not a Keithley 2636: {identity or 'no identity'}")
        except Exception:
            manager.close()
            raise
        self._manager = manager
        self._instrument = instrument

    def configure(self, *, voltage_v: float, current_mA: float) -> None:
        smu = self._smu
        device = self._device()
        current_limit_a = self.current_limit_mA / 1000.0
        current_range_a = next(
            value for value in (1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 1.5)
            if current_limit_a <= value
        )
        voltage_limit = max(0.1, float(voltage_v))
        device.write(f"{smu}.reset()")
        device.write(f"{smu}.source.func = {smu}.OUTPUT_DCAMPS")
        device.write(f"{smu}.source.autorangei = {smu}.AUTORANGE_OFF")
        device.write(f"{smu}.source.rangei = {current_range_a:.9g}")
        device.write(f"{smu}.source.limitv = {voltage_limit:.6g}")
        device.write(
            f"{smu}.sense = {smu}.SENSE_REMOTE" if self.remote_sense else f"{smu}.sense = {smu}.SENSE_LOCAL"
        )
        device.write(f"{smu}.measure.autorangei = {smu}.AUTORANGE_OFF")
        device.write(f"{smu}.measure.rangei = {current_range_a:.9g}")
        device.write(f"{smu}.measure.autorangev = {smu}.AUTORANGE_ON")
        device.write(f"{smu}.measure.nplc = 0.01")
        device.write(f"{smu}.measure.autozero = {smu}.AUTOZERO_OFF")
        device.write(f"{smu}.measure.filter.enable = {smu}.FILTER_OFF")
        device.write(f"{smu}.measure.delay = 0")
        self.set_current(current_mA)
        device.write(f"{smu}.source.output = {smu}.OUTPUT_ON")

    def set_current(self, current_mA: float) -> None:
        current = max(0.0, float(current_mA))
        if current > self.current_limit_mA + 1e-9:
            raise ValueError(
                f"Requested Keithley current {current:g} mA exceeds the "
                f"{self.current_limit_mA:g} mA run limit."
            )
        self._device().write(f"{self._smu}.source.leveli = {current / 1000.0:.9g}")

    def measure(self) -> dict[str, float]:
        device = self._device()
        response = device.query(f"print({self._smu}.measure.iv())").strip()
        values = response.replace(",", " ").split()
        if len(values) != 2:
            raise RuntimeError(f"Keithley returned an invalid I/V reading: {response!r}")
        current_a, voltage_v = (float(value) for value in values)
        return {"current_mA": current_a * 1000.0, "voltage_V": voltage_v}

    def warm_up_measurement(self) -> None:
        """Absorb the first, slower acquisition before recipe timing begins."""
        self.measure()

    def close(self) -> None:
        device = self._instrument
        manager = self._manager
        self._instrument = None
        self._manager = None
        try:
            if device is not None:
                try:
                    device.write(f"{self._smu}.source.leveli = 0")
                finally:
                    try:
                        device.write(f"{self._smu}.source.output = {self._smu}.OUTPUT_OFF")
                    finally:
                        device.close()
        finally:
            if manager is not None:
                manager.close()


@dataclass(frozen=True, slots=True)
class RunConfig:
    blocks: tuple[CurrentBlock, ...]
    output_dir: Path
    run_name: str
    control_rate_hz: float
    measurement_rate_hz: float
    ui_rate_hz: float
    initial_current_mA: float
    max_current_mA: float
    voltage_limit_v: float
    max_resistance_ohm: float | None = None
    repeat_count: int | None = 1
    resistance_action: str = "hold_current"
    resistance_check_rate_hz: float = 100.0
    record_csv: bool = True
    log_rate_hz: float | None = None
    electrical_limits: ElectricalLimits = ElectricalLimits()

    def __post_init__(self) -> None:
        if self.log_rate_hz is not None and (not math.isfinite(self.log_rate_hz) or self.log_rate_hz <= 0):
            raise ValueError("CSV rate must be positive and finite.")
        for label, value in (
            ("Control rate", self.control_rate_hz),
            ("Measurement/log rate", self.measurement_rate_hz),
            ("UI refresh rate", self.ui_rate_hz),
            ("Resistance check rate", self.resistance_check_rate_hz),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be greater than zero.")
        if self.resistance_action not in {"hold_current", "readout_current", "output_off"}:
            raise ValueError("Unknown resistance limit action.")
        if (not math.isfinite(self.initial_current_mA) or self.initial_current_mA < 0
                or not math.isfinite(self.max_current_mA)
                or self.initial_current_mA > self.max_current_mA):
            raise ValueError("Initial/readout current must be finite and within the run limit.")
        if self.max_resistance_ohm is not None and (
            not math.isfinite(self.max_resistance_ohm) or self.max_resistance_ohm <= 0
        ):
            raise ValueError("Maximum resistance must be greater than zero.")


@dataclass(frozen=True, slots=True)
class RunHistoryEntry:
    run_dir: Path
    csv_path: Path
    started_utc: str
    status: str
    rows: int | None


def find_run_history(output_dir: Path) -> list[RunHistoryEntry]:
    """Return saved current-program runs, newest first."""
    if not output_dir.is_dir():
        return []
    entries: list[RunHistoryEntry] = []
    for run_dir in output_dir.iterdir():
        if not run_dir.is_dir():
            continue
        csv_path = run_dir / "measurement.csv"
        if not csv_path.is_file():
            continue
        metadata: dict[str, Any] = {}
        metadata_path = run_dir / "metadata.json"
        if metadata_path.is_file():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    metadata = payload
            except (OSError, json.JSONDecodeError):
                pass
        rows_value = metadata.get("rows")
        try:
            rows = int(rows_value) if rows_value is not None else None
        except (TypeError, ValueError):
            rows = None
        entries.append(
            RunHistoryEntry(
                run_dir=run_dir,
                csv_path=csv_path,
                started_utc=str(metadata.get("started_utc") or ""),
                status=str(metadata.get("status") or "unknown"),
                rows=rows,
            )
        )
    return sorted(
        entries,
        key=lambda entry: (entry.started_utc, entry.run_dir.stat().st_mtime),
        reverse=True,
    )


def read_history_preview(path: Path, limit: int = 6000) -> dict[str, Any]:
    """Bounded, deterministic preview; never alters source measurement files."""
    points = []
    stride = 1
    count = 0
    last = None
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                elapsed = float(row["elapsed_s"])
                target = float(row["target_current_mA"])
                if not math.isfinite(elapsed) or not math.isfinite(target):
                    continue
            except (ValueError, TypeError, KeyError):
                continue
            def number(key):
                try: return float(row.get(key) or "nan")
                except (ValueError, TypeError): return float("nan")
            last = (elapsed, target, number("measured_current_mA"), number("resistance_ohm"))
            if count % stride == 0:
                points.append(last)
            count += 1
            if len(points) >= limit:
                points = points[::2]
                stride *= 2
    if not points:
        raise ValueError("No usable measurement rows in this run.")
    if last is not None and points[-1][0] != last[0]: points.append(last)
    return dict(points=points, count=count, sampled=stride>1)


class _PreviewSignals(QtCore.QObject):
    ready = QtCore.pyqtSignal(int, object, str)


class _VisaSignals(QtCore.QObject):
    ready = QtCore.pyqtSignal(int, str, object, str)


class _VisaDiscoveryJob(QtCore.QRunnable):
    def __init__(self, token, mode):
        super().__init__()
        self.token, self.mode = token, mode
        self.discover = list_visa_resources
        self.signals = _VisaSignals()

    def run(self):
        try:
            self.signals.ready.emit(self.token, self.mode, self.discover(), "")
        except Exception as exc:
            self.signals.ready.emit(self.token, self.mode, [], str(exc))


class _PreviewJob(QtCore.QRunnable):
    def __init__(self, token, path):
        super().__init__()
        self.token, self.path = token, path
        self.signals = _PreviewSignals()

    def run(self):
        try:
            result = read_history_preview(self.path)
            self.signals.ready.emit(self.token, result, "")
        except Exception as exc:
            self.signals.ready.emit(self.token, None, str(exc))


class HistoryPreview(QtWidgets.QWidget):
    """Selection preview isolated from the main run plots and hardware."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.token = 0
        self.pending_path = None
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._load)
        layout = QtWidgets.QVBoxLayout(self)
        self.caption = QtWidgets.QLabel("Select a run to preview")
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.curves = []
        if pg is not None:
            for title, x, y in [("Current vs time", "Time (s)", "Current (mA)"),
                                ("Resistance vs time", "Time (s)", "Resistance (Ω)"),
                                ("Resistance vs current", "Measured current (mA)", "Resistance (Ω)")]:
                plot = pg.PlotWidget(title=title)
                plot.setLabel("bottom", x)
                plot.setLabel("left", y)
                plot.showGrid(x=True,y=True,alpha=.15)
                layout.addWidget(plot)
                if not self.curves:
                    plot.addLegend()
                    self.curves.append(plot.plot(pen=pg.mkPen("#51b5e5",width=1.5),name="Target"))
                    self.curves.append(plot.plot(pen=pg.mkPen("#e6b45b",width=1.2),name="Measured"))
                else:
                    self.curves.append(plot.plot(pen=pg.mkPen("#b79aff",width=1.3)))

    def select_path(self, path):
        self.token += 1
        self.pending_path = Path(path)
        for curve in self.curves: curve.clear()
        self.caption.setText(f"Loading preview: {self.pending_path.parent.name}")
        self.timer.start()

    def _load(self):
        job = _PreviewJob(self.token,self.pending_path)
        job.signals.ready.connect(self._ready)
        QtCore.QThreadPool.globalInstance().start(job)

    @QtCore.pyqtSlot(int, object, str)
    def _ready(self, token, result, error):
        if token != self.token: return
        if error:
            self.caption.setText(f"Preview unavailable: {error}")
            return
        suffix = " · sampled overview; brief events may be omitted" if result['sampled'] else ""
        self.caption.setText(f"{self.pending_path.parent.name}\n{result['count']:,} rows{suffix}")
        if not self.curves:
            self.caption.setText(self.caption.text()+"\nGraph previews require pyqtgraph.")
            return
        t, target, measured, resistance = zip(*result['points'])
        for curve,x,y in zip(self.curves,(t,t,t,measured),(target,measured,resistance,resistance)):
            curve.setData(x,y,connect="finite")


class ProgramWorker(QtCore.QThread):
    sample_ready = QtCore.pyqtSignal(dict)
    state_changed = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)
    paths_ready = QtCore.pyqtSignal(str, str)

    def __init__(self, config: RunConfig, adapter: SupplyAdapter, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.adapter = adapter
        self._stop_event = Event()
        self._commands: SimpleQueue = SimpleQueue()
        self.ui_pending = Event()
        self.coalesce_ui = False

    def request_readout(self) -> None:
        self._commands.put(("readout", None))

    def request_sequence(self, blocks: Sequence[CurrentBlock], repeat_count: int | None) -> None:
        validate_recipe(blocks, max_current_mA=self.config.max_current_mA, repeat_count=repeat_count)
        profile = PROFILES.get(getattr(self.adapter, "profile_mode", ""))
        if profile is not None and not profile.conditional_steps and any(b.resistance_ohm is not None for b in blocks):
            raise ValueError("Resistance-conditioned steps are not qualified for this supply.")
        self._commands.put(("sequence", (tuple(blocks), repeat_count)))

    def stop(self) -> None:
        self._stop_event.set()

    def _wait_until(self, deadline: float) -> None:
        """Wait interruptibly, using the high-resolution timer near the deadline."""
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if remaining > 0.025:
                self._stop_event.wait(remaining - 0.015)
            else:
                time.sleep(remaining)

    def _startup_baseline(self, monitor, metadata):
        """Qualify slow PSU startup at the initial current, before recipe timing.

        Existing fault detectors run on every sample; no protection is muted.
        Only finite zero readbacks can be retried, under a bounded deadline.
        """
        timeout = getattr(self.adapter, "startup_timeout_s", 0.0)
        started = time.monotonic()
        samples = metadata["startup_samples"] = []
        valid_count = 0
        while not self._stop_event.is_set():
            baseline = self.adapter.measure()
            now = time.monotonic()
            current, voltage = baseline.get("current_mA"), baseline.get("voltage_V")
            samples.append(dict(elapsed_s=now-started, **baseline))
            finite = current is not None and voltage is not None and all(
                math.isfinite(float(v)) and float(v) >= 0 for v in (current, voltage))
            fault = monitor.check(now, self.config.initial_current_mA, current, voltage)
            if not finite or fault or timeout <= 0:
                return baseline
            valid = self.config.initial_current_mA == 0 or (current > 0 and voltage > 0)
            # Never defer the legacy upper-R limit while waiting for settling.
            if current > 0 and self.config.max_resistance_ohm is not None:
                if voltage / (current / 1000) >= self.config.max_resistance_ohm:
                    return baseline
            valid_count = valid_count + 1 if valid else 0
            if valid_count >= 2:
                return baseline
            if now - started >= timeout:
                raise RuntimeError(
                    f"Startup baseline unavailable after {now-started:.2f} s at initial "
                    f"{self.config.initial_current_mA:g} mA: measured I={current!r} mA, "
                    f"V={voltage!r} V. Output will be shut down; the ramp was not started. "
                    "Check connections and the instrument display; zero readback can mean "
                    "unsettled/low-resolution telemetry, an open circuit or a short."
                )
            self._wait_until(now + getattr(self.adapter, "startup_poll_s", .1))
        return None

    def run(self) -> None:  # noqa: C901 - cleanup is deliberately kept in one ownership scope
        started_utc = _utc_now()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in self.config.run_name).strip()
        safe_name = safe_name or "current-program"
        run_dir = self.config.output_dir / f"{safe_name}_{stamp}"
        csv_path = run_dir / "measurement.csv"
        metadata_path = run_dir / "metadata.json"
        engine = make_recipe_engine(
            self.config.blocks,
            initial_current_mA=self.config.initial_current_mA,
            repeat_count=self.config.repeat_count,
        )
        error: str | None = None
        rows = 0
        last_target: float | None = None
        resistance_current_ceiling_mA: float | None = None
        resistance_limit_reached_elapsed_s: float | None = None
        opened = False
        protection_checks = 0
        last_check_time: float | None = None
        max_check_gap_s = 0.0
        readout_mode = False
        sequence_id = 1
        sequence_events: deque[dict[str, Any]] = deque(maxlen=1024)
        monitor = ElectricalMonitor(self.config.electrical_limits)
        fault_sample = None
        writer = None
        sleep_guard = None
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            metadata = {
                "schema": "current_program_logger_v1",
                "resistance_settling_s": getattr(self.adapter, "settling_s", 0.0),
                "started_utc": started_utc,
                "finished_utc": None,
                "status": "running",
                "supply": self.adapter.description,
                "control_rate_hz": self.config.control_rate_hz,
                "measurement_rate_hz": self.config.measurement_rate_hz,
                "ui_rate_hz": self.config.ui_rate_hz,
                "initial_current_mA": self.config.initial_current_mA,
                "max_current_mA": self.config.max_current_mA,
                "voltage_limit_v": self.config.voltage_limit_v,
                "max_resistance_ohm": self.config.max_resistance_ohm,
                "repeat_count": self.config.repeat_count,
                "resistance_action": self.config.resistance_action,
                "resistance_check_rate_hz": self.config.resistance_check_rate_hz,
                "blocks": [asdict(block) for block in self.config.blocks],
                "record_csv": self.config.record_csv,
                "log_rate_hz": self.config.log_rate_hz or self.config.measurement_rate_hz,
                "electrical_limits": asdict(self.config.electrical_limits),
            }
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            self.paths_ready.emit(str(csv_path) if self.config.record_csv else "", str(metadata_path))
            profile = PROFILES.get(getattr(self.adapter, "profile_mode", ""))
            if profile is not None:
                rates = (self.config.measurement_rate_hz, self.config.resistance_check_rate_hz,
                         self.config.ui_rate_hz, self.config.log_rate_hz or self.config.measurement_rate_hz)
                if self.config.control_rate_hz > profile.max_control_hz or max(rates) > profile.max_acquisition_hz:
                    raise ValueError("Requested rates exceed this supply's application ceilings; review its profile before starting.")
                if not profile.conditional_steps and isinstance(engine, ConditionalRecipeEngine):
                    raise ValueError("Resistance-conditioned steps are not qualified for this supply.")
            if self.config.record_csv:
                writer = BufferedCsv(csv_path, CSV_FIELDS)
                writer.start()
            sleep_guard = create_experiment_sleep_guard(APP_TITLE, keep_display_awake=False)
            sleep_guard.acquire()
            if self._stop_event.is_set():
                return
            self.adapter.open()
            opened = True
            if self._stop_event.is_set():
                return
            self.adapter.configure(
                voltage_v=self.config.voltage_limit_v,
                current_mA=self.config.initial_current_mA,
            )
            warm_up = getattr(self.adapter, "warm_up_measurement", None)
            if callable(warm_up):
                warm_up_started = time.monotonic()
                warm_up()
                metadata["warm_up_measurement_s"] = time.monotonic() - warm_up_started
            if (monitor.limits.enabled or self.config.max_resistance_ohm is not None
                    or getattr(self.adapter, "startup_timeout_s", 0)):
                baseline = self._startup_baseline(monitor, metadata)
                if baseline is None:
                    return
                baseline_current = baseline.get("current_mA")
                baseline_voltage = baseline.get("voltage_V")
                fault = monitor.check(time.monotonic(), self.config.initial_current_mA,
                                      baseline_current, baseline_voltage)
                if fault:
                    fault_sample = dict(timestamp_utc=_utc_now(), elapsed_s=0,
                                        target_current_mA=self.config.initial_current_mA,
                                        measured_current_mA=baseline_current,
                                        voltage_V=baseline_voltage, electrical_fault=fault)
                    self.adapter.close()
                    opened = False
                    if writer is not None:
                        writer.submit(fault_sample, flush=True)
                    raise RuntimeError(f"Electrical fault latched at baseline: {fault}")
                baseline_resistance: float | None = None
                if baseline_current is not None and baseline_voltage is not None:
                    current_mA = float(baseline_current)
                    if abs(current_mA) > 1e-9:
                        baseline_resistance = float(baseline_voltage) / (current_mA / 1000.0)
                metadata["baseline_resistance_ohm"] = baseline_resistance
                if (baseline_current is None or baseline_voltage is None
                        or not math.isfinite(float(baseline_current))
                        or not math.isfinite(float(baseline_voltage))
                        or (self.config.max_resistance_ohm is not None and self.config.initial_current_mA > 0 and not (
                            monitor.limits.open_current_fraction is not None
                            and self.config.initial_current_mA >= monitor.limits.active_above_mA
                            and baseline_current == 0
                        ) and (
                            baseline_resistance is None or not math.isfinite(baseline_resistance)
                            or baseline_resistance <= 0))):
                    raise RuntimeError(f"Invalid baseline protection measurement: I={baseline_current!r} mA, "
                                       f"V={baseline_voltage!r} V, R={baseline_resistance!r} ohm; stopping output.")
                if (
                    self.config.max_resistance_ohm is not None
                    and baseline_resistance is not None
                    and math.isfinite(baseline_resistance)
                    and baseline_resistance >= self.config.max_resistance_ohm
                ):
                    resistance_current_ceiling_mA = self.config.initial_current_mA
                    resistance_limit_reached_elapsed_s = 0.0
                    if self.config.resistance_action == "output_off":
                        self.adapter.close()
                        opened = False
                        self._stop_event.set()
            start = time.monotonic()
            sequence_start = start
            control_interval_s = 1.0 / self.config.control_rate_hz
            measurement_interval_s = 1.0 / (self.config.log_rate_hz or self.config.measurement_rate_hz)
            acquisition_interval_s = 1.0 / max(
                self.config.measurement_rate_hz,
                self.config.ui_rate_hz,
                self.config.log_rate_hz or 0.0,
                self.config.resistance_check_rate_hz if (monitor.limits.enabled or self.config.max_resistance_ohm is not None
                    or isinstance(engine, ConditionalRecipeEngine)) else 0.0,
            )
            ui_interval_s = 1.0 / self.config.ui_rate_hz
            next_control = start
            next_measurement = start
            next_log = start
            next_ui = start
            state = engine.state_at(0.0)
            target = min(self.config.max_current_mA, max(0.0, state.target_mA))
            while not self._stop_event.is_set():
                if writer is not None:
                    writer.check()
                now = time.monotonic()
                try:
                    command, payload = self._commands.get_nowait()
                except Empty:
                    command, payload = None, None
                if command == "readout":
                    readout_mode = True
                    target = self.config.initial_current_mA
                    if resistance_current_ceiling_mA is not None:
                        target = min(target, resistance_current_ceiling_mA)
                    self.adapter.set_current(target)
                    last_target = target
                    sequence_events.append(dict(event="readout", elapsed_s=now-start, sequence_id=sequence_id))
                    next_control = now
                    next_ui = now
                elif command == "sequence" and readout_mode and resistance_current_ceiling_mA is None:
                    blocks, repeat_count = payload
                    engine = make_recipe_engine(blocks, initial_current_mA=float(last_target), repeat_count=repeat_count)
                    acquisition_interval_s = 1.0 / max(self.config.measurement_rate_hz,
                        self.config.ui_rate_hz, self.config.log_rate_hz or 0.0,
                        self.config.resistance_check_rate_hz if (monitor.limits.enabled or self.config.max_resistance_ohm is not None
                            or isinstance(engine, ConditionalRecipeEngine)) else 0.0)
                    next_measurement = now
                    sequence_start = now
                    sequence_id += 1
                    readout_mode = False
                    sequence_events.append(dict(event="sequence", elapsed_s=now-start, sequence_id=sequence_id,
                                                blocks=[asdict(b) for b in blocks], repeat_count=repeat_count))
                    next_control = now
                    next_ui = now
                if now >= next_control:
                    state = engine.state_at(now - sequence_start)
                    if not readout_mode and getattr(engine, "timed_out", False):
                        readout_mode = True
                        sequence_events.append(dict(event="resistance_timeout",elapsed_s=now-start,
                                                    sequence_id=sequence_id,block_index=state.block_index))
                        next_ui = now
                    if readout_mode:
                        state = ProgramState(0, 0, CurrentBlock("Hold", self.config.initial_current_mA, None, "Readout"),
                                             self.config.initial_current_mA, 0, True)
                    target = min(self.config.max_current_mA, max(0.0, state.target_mA))
                    if resistance_current_ceiling_mA is not None:
                        if self.config.resistance_action == "readout_current":
                            target = resistance_current_ceiling_mA
                        else:
                            target = min(target, resistance_current_ceiling_mA)
                if now >= next_control and (last_target is None or abs(target - last_target) >= 1e-9):
                    self.adapter.set_current(target)
                    last_target = target
                if now >= next_control:
                    next_control = max(next_control + control_interval_s, now + control_interval_s)
                if now >= next_measurement:
                    resistance_quality = getattr(self.adapter, "resistance_quality", lambda: "unfiltered")()
                    readback = self.adapter.measure()
                    acquired_at = time.monotonic()
                    if monitor.limits.enabled or self.config.max_resistance_ohm is not None:
                        protection_checks += 1
                        if last_check_time is not None:
                            max_check_gap_s = max(max_check_gap_s, acquired_at - last_check_time)
                        last_check_time = acquired_at
                    measured = readback.get("current_mA")
                    voltage = readback.get("voltage_V")
                    fault = monitor.check(acquired_at, target, measured, voltage)
                    if fault:
                        fault_sample = dict(timestamp_utc=_utc_now(), elapsed_s=acquired_at-start,
                                            target_current_mA=target, measured_current_mA=measured,
                                            voltage_V=voltage, electrical_fault=fault)
                        # Never enqueue disk/UI work before the shutdown attempt.
                        self.adapter.close()
                        opened = False
                        if writer is not None:
                            writer.submit(fault_sample, flush=True)
                        raise RuntimeError(f"Electrical fault latched: {fault}")
                    resistance = None
                    power = None
                    if measured is not None and voltage is not None:
                        if abs(float(measured)) > 1e-9:
                            resistance = float(voltage) / (float(measured) / 1000.0)
                            power = (float(measured) ** 2) * resistance / 1000.0
                        else:
                            power = 0.0
                    if self.config.max_resistance_ohm is not None and (
                        measured is None or voltage is None
                        or not math.isfinite(float(measured))
                        or not math.isfinite(float(voltage))
                        or (target > 0 and not (
                            monitor.limits.open_current_fraction is not None
                            and target >= monitor.limits.active_above_mA and measured == 0
                        ) and (resistance is None or not math.isfinite(resistance) or resistance <= 0))
                    ):
                        raise RuntimeError("Invalid resistance protection measurement; stopping output.")
                    tripped_now = False
                    if (
                        resistance_current_ceiling_mA is None
                        and self.config.max_resistance_ohm is not None
                        and resistance is not None
                        and math.isfinite(resistance)
                        and resistance >= self.config.max_resistance_ohm
                    ):
                        resistance_current_ceiling_mA = max(
                            0.0,
                            float(last_target if last_target is not None else target),
                        )
                        resistance_limit_reached_elapsed_s = acquired_at - start
                        tripped_now = True
                        if self.config.resistance_action == "readout_current":
                            resistance_current_ceiling_mA = min(
                                resistance_current_ceiling_mA,
                                self.config.initial_current_mA,
                            )
                            self.adapter.set_current(resistance_current_ceiling_mA)
                            last_target = resistance_current_ceiling_mA
                        if self.config.resistance_action == "output_off":
                            # Shut down before logging, UI signals, or another recipe command.
                            self.adapter.close()
                            opened = False
                            self._stop_event.set()
                    if (not readout_mode and resistance_current_ceiling_mA is None
                            and isinstance(engine, ConditionalRecipeEngine)):
                        if engine.observe(acquired_at-sequence_start, resistance,
                                          (state.cycle_index, state.block_index)):
                            sequence_events.append(dict(event="resistance_target",elapsed_s=acquired_at-start,
                                                        sequence_id=sequence_id,block_index=state.block_index))
                            next_control = acquired_at
                            next_ui = now
                    row = {
                        "timestamp_utc": _utc_now(),
                        "elapsed_s": now - start,
                        "cycle_index": state.cycle_index + 1,
                        "block_index": state.block_index + 1,
                        "block_type": state.block.kind,
                        "block_label": state.block.label,
                        "sequence_complete": state.sequence_complete,
                        "target_current_mA": target,
                        "measured_current_mA": measured,
                        "voltage_V": voltage,
                        "resistance_ohm": resistance,
                        "qualified_resistance_ohm": resistance if resistance_quality != "settling" else None,
                        "resistance_quality": resistance_quality,
                        "power_mW": power,
                        "resistance_limit_reached": resistance_current_ceiling_mA is not None,
                        "resistance_current_ceiling_mA": resistance_current_ceiling_mA,
                        "operating_mode": "readout" if readout_mode else "sequence",
                        "sequence_id": sequence_id,
                        "sequence_elapsed_s": now - sequence_start,
                    }
                    if acquired_at >= next_log or tripped_now:
                        if writer is not None:
                            writer.submit(row, flush=tripped_now)
                        next_log = acquired_at + measurement_interval_s
                    if (now >= next_ui or tripped_now) and (not self.coalesce_ui or not self.ui_pending.is_set()):
                        self.ui_pending.set()
                        self.sample_ready.emit(row)
                        self.state_changed.emit(
                            {
                                "cycle_index": state.cycle_index,
                                "block_index": state.block_index,
                                "block_type": state.block.kind,
                                "block_label": state.block.label,
                                "sequence_complete": state.sequence_complete,
                                "resistance_limit_reached": resistance_current_ceiling_mA
                                is not None,
                                "resistance_current_ceiling_mA": resistance_current_ceiling_mA,
                                "resistance_action": self.config.resistance_action,
                                "readout_mode": readout_mode,
                                "resistance_timeout": getattr(engine, "timed_out", False),
                            }
                        )
                        next_ui = max(next_ui + ui_interval_s, now + ui_interval_s)
                    next_measurement = max(
                        next_measurement + acquisition_interval_s,
                        now + acquisition_interval_s,
                    )
                next_deadline = min(next_control, next_measurement)
                self._wait_until(next_deadline)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if opened:
                try:
                    self.adapter.close()
                except Exception as exc:
                    error = f"{error or ''} Safe shutdown failed: {exc}".strip()
            if sleep_guard is not None:
                try:
                    sleep_guard.release()
                except Exception as exc:
                    error = f"{error or ''} Sleep guard release failed: {exc}".strip()
            if writer is not None:
                try:
                    writer.close()
                except Exception as exc:
                    error = f"{error or ''} {exc}".strip()
                rows = writer.rows
            if metadata_path.exists():
                try:
                    payload = metadata
                    payload.update(
                        finished_utc=_utc_now(),
                        status="failed" if error else "stopped",
                        rows=rows,
                        error=error,
                        resistance_limit_reached=resistance_current_ceiling_mA is not None,
                        resistance_current_ceiling_mA=resistance_current_ceiling_mA,
                        resistance_limit_reached_elapsed_s=resistance_limit_reached_elapsed_s,
                        resistance_action=self.config.resistance_action,
                        protection_checks=protection_checks,
                        max_protection_check_gap_s=max_check_gap_s,
                        sequence_events=list(sequence_events),
                        sequence_events_retained_limit=1024,
                        electrical_fault=monitor.fault,
                        fault_sample=fault_sample,
                    )
                    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                except Exception:
                    pass
            if error:
                self.failed.emit(error)


def bundle_estimate(load_g: float, current_mA: float, resistance_ohm: float) -> tuple[int, float, float]:
    """Ideal equal-load-sharing 2 kg bundle; electrical estimate, not stroke validation."""
    if not all(math.isfinite(v) for v in (load_g, current_mA, resistance_ohm)):
        raise ValueError("Bundle inputs must be finite.")
    if load_g <= 0 or current_mA < 0 or resistance_ohm <= 0:
        raise ValueError("Positive load/resistance and nonnegative current required.")
    wires = math.ceil(2000.0 / load_g)
    per_wire_w = (current_mA / 1000.0) ** 2 * resistance_ohm
    return wires, per_wire_w, wires * per_wire_w


class CurrentProgramWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(APP_TITLE)
        self.resize(1360, 820)
        self.settings = QtCore.QSettings("MicrowireData", "CurrentProgramLogger")
        self.worker: ProgramWorker | None = None
        self._readout_active = False
        self._stopping = False
        self._fault_latched = False
        self._times: list[float] = []
        self._targets: list[float] = []
        self._measured: list[float] = []
        self._resistance: list[float] = []
        self._viewing_history = False
        self._loading_profile = True
        self._active_supply_mode = None
        self._discovery_token = 0
        self._discovery_busy = False
        self._discovery_jobs = {}
        self._discovery_timer = QtCore.QTimer(self)
        self._discovery_timer.setSingleShot(True)
        self._discovery_timer.setInterval(5000)
        self._discovery_timer.timeout.connect(self._visa_discovery_timeout)
        self._build_ui()
        # Scope the application filter to this window, including dynamically added recipe editors.
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._load_settings()
        self._loading_profile = False
        self.bundle_timer = QtCore.QTimer(self)
        self.bundle_timer.setInterval(500)
        self.bundle_timer.timeout.connect(self._refresh_bundle_estimate)
        self.bundle_timer.start()
        self._refresh_bundle_estimate()
        install_standard_menu(self, help_topic="logger_current_annealing")
        if PROFILES[self.mode_combo.currentText()].vendor:
            self._refresh_visa_resources()

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Type.Wheel and isinstance(watched, QtWidgets.QWidget):
            control = watched
            if isinstance(control, QtWidgets.QLineEdit):
                control = control.parentWidget()
            if (isinstance(control, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox))
                    and self.isAncestorOf(control)):
                # Even a focused field must not change while scrolling the setup panel.
                # Explicit typing, keyboard arrows, spin buttons and popup selection still work.
                parent = control.parentWidget()
                while parent is not None and not isinstance(parent, QtWidgets.QAbstractScrollArea):
                    parent = parent.parentWidget()
                if parent is not None:
                    viewport = parent.viewport()
                    forwarded = QtGui.QWheelEvent(
                        QtCore.QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                        event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                        event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                    )
                    QtWidgets.QApplication.sendEvent(viewport, forwarded)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        title = QtWidgets.QLabel(APP_TITLE)
        font = title.font()
        font.setPointSize(font.pointSize() + 6)
        font.setBold(True)
        title.setFont(font)
        root.addWidget(title)
        subtitle = QtWidgets.QLabel(
            "Build a reusable current sequence while voltage and current are logged continuously."
        )
        root.addWidget(subtitle)

        splitter = QtWidgets.QSplitter()
        root.addWidget(splitter, 1)
        splitter.addWidget(self._build_setup_panel())
        splitter.addWidget(self._build_recipe_panel())
        splitter.addWidget(self._build_live_panel())
        splitter.setSizes([400, 530, 430])

    def _build_setup_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        connection = QtWidgets.QGroupBox("Connection")
        form = QtWidgets.QFormLayout(connection)
        self.connection_form = form
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Simulation", "Keithley 2636B (VISA)", "Shared HMP broker", "Siglent SPD1305X (VISA)"])
        self.host_edit = QtWidgets.QLineEdit("127.0.0.1")
        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(8765)
        self.channel_spin = QtWidgets.QSpinBox()
        self.channel_spin.setRange(1, 4)
        self.channel_spin.setValue(1)
        self.visa_resource_combo = QtWidgets.QComboBox()
        self.visa_resource_combo.setEditable(True)
        self.visa_resource_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.visa_resource_combo.setPlaceholderText("USB0::0x05E6::0x2636::…::INSTR")
        self.visa_refresh_button = QtWidgets.QPushButton("Refresh")
        self.visa_refresh_button.clicked.connect(self._refresh_visa_resources)
        visa_row = QtWidgets.QHBoxLayout()
        self.visa_row = visa_row
        visa_row.addWidget(self.visa_resource_combo, 1)
        visa_row.addWidget(self.visa_refresh_button)
        self.visa_status_label = QtWidgets.QLabel()
        self.visa_status_label.setWordWrap(True)
        self.profile_label = QtWidgets.QLabel()
        self.profile_label.setWordWrap(True)
        self.keithley_channel_combo = QtWidgets.QComboBox()
        self.keithley_channel_combo.addItems(["A", "B"])
        self.remote_sense_check = QtWidgets.QCheckBox("Use 4-wire remote sense")
        self.remote_sense_check.setChecked(True)
        self.voltage_spin = QtWidgets.QDoubleSpinBox()
        self.voltage_spin.setRange(0.1, 32.05)
        self.voltage_spin.setValue(1.0)
        self.voltage_spin.setSuffix(" V")
        self.max_current_spin = QtWidgets.QDoubleSpinBox()
        self.max_current_spin.setRange(1.0, 5000.0)
        self.max_current_spin.setValue(5.0)
        self.max_current_spin.setSuffix(" mA")
        self.initial_current_spin = QtWidgets.QDoubleSpinBox()
        self.initial_current_spin.setRange(0.0, 5000.0)
        self.initial_current_spin.setValue(1.0)
        self.initial_current_spin.setSuffix(" mA")
        self.resistance_limit_check = QtWidgets.QCheckBox("Enable")
        self.resistance_limit_check.setChecked(True)
        self.max_resistance_spin = QtWidgets.QDoubleSpinBox()
        self.max_resistance_spin.setRange(0.01, 1_000_000.0)
        self.max_resistance_spin.setDecimals(2)
        self.max_resistance_spin.setValue(190.0)
        self.max_resistance_spin.setSuffix(" Ω")
        resistance_limit_help = (
            "Checked at the independent resistance check rate. Select either a latched "
            "current ceiling, a drop to initial/readout current with logging, or output shutdown. "
            "Readout mode stays latched until Stop; it does not regulate resistance."
        )
        self.initial_current_spin.setToolTip(
            "Initial current and post-trip readout current. Readout mode never increases "
            "current at a trip. A nonzero value permits V/I measurements but still heats the wire."
        )
        self.resistance_limit_check.setToolTip(resistance_limit_help)
        self.max_resistance_spin.setToolTip(resistance_limit_help)
        resistance_limit_row = QtWidgets.QHBoxLayout()
        resistance_limit_row.addWidget(self.resistance_limit_check)
        resistance_limit_row.addWidget(self.max_resistance_spin, 1)
        self.resistance_action_combo = QtWidgets.QComboBox()
        self.resistance_action_combo.addItem("Hold current ceiling (legacy)", "hold_current")
        self.resistance_action_combo.addItem("Drop to initial/readout current; keep logging", "readout_current")
        self.resistance_action_combo.addItem("Zero current / output off; stop run", "output_off")
        self.resistance_check_rate_spin = QtWidgets.QDoubleSpinBox()
        self.resistance_check_rate_spin.setRange(1.0, 1000.0)
        self.resistance_check_rate_spin.setValue(100.0)
        self.resistance_check_rate_spin.setSuffix(" Hz")
        self.resistance_check_rate_spin.setToolTip(
            "Requested protection sampling rate, independent of CSV/UI rates. Default 100 Hz "
            "(10 ms). Actual speed depends on instrument and communication latency; not a "
            "hardware interlock. Observed maximum check gap is saved in metadata."
        )
        self.control_rate_spin = QtWidgets.QDoubleSpinBox()
        self.control_rate_spin.setRange(0.1, 500.0)
        self.control_rate_spin.setDecimals(1)
        self.control_rate_spin.setValue(100.0)
        self.control_rate_spin.setSuffix(" Hz")
        self.measurement_rate_spin = QtWidgets.QDoubleSpinBox()
        self.measurement_rate_spin.setRange(0.01, 1000.0)
        self.measurement_rate_spin.setDecimals(2)
        self.measurement_rate_spin.setValue(10.0)
        self.measurement_rate_spin.setSuffix(" Hz")
        self.ui_rate_spin = QtWidgets.QDoubleSpinBox()
        self.ui_rate_spin.setRange(0.1, 60.0)
        self.ui_rate_spin.setDecimals(1)
        self.ui_rate_spin.setValue(10.0)
        self.ui_rate_spin.setSuffix(" Hz")
        self.settling_spin = QtWidgets.QDoubleSpinBox()
        self.settling_spin.setRange(0, 10)
        self.settling_spin.setValue(1.0)
        self.settling_spin.setSuffix(" s")
        self.settling_spin.setToolTip("Provisional Siglent resistance qualification delay after each real current step. 0 disables it. Raw CSV and protection remain active; elapsed time does not guarantee ADC synchronization.")
        self.quality_label = QtWidgets.QLabel("Resistance: awaiting measurements")
        for label, widget in (
            ("Mode", self.mode_combo),
            ("Broker host", self.host_edit),
            ("Broker port", self.port_spin),
            ("HMP channel", self.channel_spin),
            ("VISA resource", visa_row),
            ("Keithley channel", self.keithley_channel_combo),
            ("Sense", self.remote_sense_check),
            ("Voltage limit", self.voltage_spin),
            ("Run current limit", self.max_current_spin),
            ("Initial current", self.initial_current_spin),
            ("Max resistance", resistance_limit_row),
            ("At resistance limit", self.resistance_action_combo),
            ("Protection poll rate", self.resistance_check_rate_spin),
            ("Control rate", self.control_rate_spin),
            ("Readback poll rate", self.measurement_rate_spin),
            ("UI refresh rate", self.ui_rate_spin),
            ("Resistance settling", self.settling_spin),
        ):
            form.addRow(label, widget)
        form.addRow(self.visa_status_label)
        form.addRow(self.profile_label)
        form.addRow(self.quality_label)
        layout.addWidget(connection)

        safety = QtWidgets.QGroupBox("Electrical faults: OFF + manual reset")
        safety_form = QtWidgets.QFormLayout(safety)
        safety_form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        self.short_check = QtWidgets.QCheckBox("Short-circuit detection")
        self.short_resistance_spin = QtWidgets.QDoubleSpinBox()
        self.short_resistance_spin.setRange(0.01, 1_000_000)
        self.short_resistance_spin.setValue(10)
        self.short_resistance_spin.setSuffix(" ohm")
        self.open_check = QtWidgets.QCheckBox("Broken wire / contact loss")
        self.open_fraction_spin = QtWidgets.QDoubleSpinBox()
        self.open_fraction_spin.setRange(1, 99)
        self.open_fraction_spin.setValue(25)
        self.open_fraction_spin.setSuffix(" % of target")
        self.protection_floor_spin = QtWidgets.QDoubleSpinBox()
        self.protection_floor_spin.setRange(0.01, 5000)
        self.protection_floor_spin.setValue(10)
        self.protection_floor_spin.setSuffix(" mA")
        self.open_confirm_spin = QtWidgets.QDoubleSpinBox()
        self.open_confirm_spin.setRange(0.01, 10)
        self.open_confirm_spin.setValue(0.2)
        self.open_confirm_spin.setSuffix(" s")
        for label, widget in (
            ("", self.short_check), ("Short if R <=", self.short_resistance_spin),
            ("", self.open_check), ("Lost if I <", self.open_fraction_spin),
            ("Active at target >=", self.protection_floor_spin),
            ("Low-current confirmation", self.open_confirm_spin),
        ):
            safety_form.addRow(label, widget)
        safety_help = QtWidgets.QLabel(
            "Set specimen-specific thresholds before enabling. No short/open detection below "
            "the target-current floor; short detection also requires measured current >= floor. "
            "Shorts trip on one sample; contact loss must persist. Neither is a hardware interlock. "
            "Windows idle sleep is prevented during runs; the screen may turn off."
        )
        safety_help.setWordWrap(True)
        safety_form.addRow(safety_help)
        self.reset_fault_button = QtWidgets.QPushButton("Acknowledge fault / allow new run")
        self.reset_fault_button.clicked.connect(self._reset_fault)
        self.reset_fault_button.setEnabled(False)
        safety_form.addRow(self.reset_fault_button)
        layout.addWidget(safety)

        execution = QtWidgets.QGroupBox("Execution")
        execution_form = QtWidgets.QFormLayout(execution)
        self.repeat_mode_combo = QtWidgets.QComboBox()
        self.repeat_mode_combo.addItems(["Once", "Fixed cycles", "Forever"])
        self.repeat_count_spin = QtWidgets.QSpinBox()
        self.repeat_count_spin.setRange(2, 1_000_000)
        self.repeat_count_spin.setValue(2)
        execution_form.addRow("Repeat", self.repeat_mode_combo)
        execution_form.addRow("Cycles", self.repeat_count_spin)
        layout.addWidget(execution)

        output = QtWidgets.QGroupBox("Continuous log")
        output_form = QtWidgets.QFormLayout(output)
        output_form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        self.record_csv_check = QtWidgets.QCheckBox("Record measurements to CSV")
        self.record_csv_check.setChecked(True)
        self.record_csv_check.setToolTip("When disabled, only run settings and fault/summary metadata are saved. Protection stays active.")
        self.log_rate_spin = QtWidgets.QDoubleSpinBox()
        self.log_rate_spin.setRange(0.01, 1000)
        self.log_rate_spin.setDecimals(2)
        self.log_rate_spin.setValue(1)
        self.log_rate_spin.setSuffix(" Hz")
        self.live_points_spin = QtWidgets.QSpinBox()
        self.live_points_spin.setRange(100, 20_000)
        self.live_points_spin.setValue(10_000)
        output_form.addRow(self.record_csv_check)
        output_form.addRow("CSV rate", self.log_rate_spin)
        output_form.addRow("Live history points", self.live_points_spin)
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.textChanged.connect(self.output_edit.setToolTip)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse_output)
        output_row = QtWidgets.QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse)
        output_form.addRow("Folder", output_row)
        self.name_edit = QtWidgets.QLineEdit("current-program")
        output_form.addRow("Run name", self.name_edit)
        self.history_button = QtWidgets.QPushButton("Measurement history…")
        self.history_button.clicked.connect(self._show_measurement_history)
        output_form.addRow(self.history_button)
        layout.addWidget(output)
        layout.addStretch(1)
        self.mode_combo.currentIndexChanged.connect(self._on_supply_changed)
        self.repeat_mode_combo.currentIndexChanged.connect(self._sync_repeat_fields)
        self.resistance_limit_check.toggled.connect(self.max_resistance_spin.setEnabled)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        return scroll

    def _build_recipe_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        heading = QtWidgets.QHBoxLayout()
        label = QtWidgets.QLabel("Program blocks")
        label_font = label.font()
        label_font.setBold(True)
        label.setFont(label_font)
        heading.addWidget(label)
        heading.addStretch(1)
        self.import_button = QtWidgets.QPushButton("Import…")
        self.export_button = QtWidgets.QPushButton("Export…")
        self.import_button.clicked.connect(self._import_recipe)
        self.export_button.clicked.connect(self._export_recipe)
        heading.addWidget(self.import_button)
        heading.addWidget(self.export_button)
        layout.addLayout(heading)

        self.table = QtWidgets.QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(("#", "Type", "Target", "Duration / max", "∞", "Label", "End condition", "R target"))
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.recipe_edit_buttons: list[QtWidgets.QPushButton] = []
        add_row = QtWidgets.QHBoxLayout()
        for kind in BLOCK_KINDS:
            button = QtWidgets.QPushButton(f"+ {kind}")
            button.clicked.connect(lambda _checked=False, value=kind: self.add_block(value))
            add_row.addWidget(button)
            self.recipe_edit_buttons.append(button)
        layout.addLayout(add_row)
        edit_row = QtWidgets.QHBoxLayout()
        for text, callback in (
            ("Duplicate", self._duplicate_selected),
            ("Remove", self._remove_selected),
            ("Move up", lambda: self._move_selected(-1)),
            ("Move down", lambda: self._move_selected(1)),
        ):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(callback)
            edit_row.addWidget(button)
            self.recipe_edit_buttons.append(button)
        layout.addLayout(edit_row)
        self.add_block("Hold", target_mA=1.0, duration_s=3.0, label="Initial readout")
        self.add_block("Ramp", target_mA=4.0, duration_s=2.0, label="Low-current test ramp")
        self.add_block("Hold", target_mA=1.0, duration_s=None, label="Measure at low current")
        return panel

    def _build_live_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        status_group = QtWidgets.QGroupBox("Run status")
        status_layout = QtWidgets.QFormLayout(status_group)
        self.status_label = QtWidgets.QLabel("Ready")
        self.block_label = QtWidgets.QLabel("—")
        self.elapsed_label = QtWidgets.QLabel("0.0 s")
        status_layout.addRow("State", self.status_label)
        status_layout.addRow("Block", self.block_label)
        status_layout.addRow("Elapsed", self.elapsed_label)
        layout.addWidget(status_group)
        self.supply_warning_label = QtWidgets.QLabel(
            "SPD1305X: 1 mA current steps (rounded down); local 2-wire sensing. "
            "Low-current resistance is approximate. Separate I/V queries; requested rates are not guaranteed. "
            "Not qualified for XRD fault protection."
        )
        self.supply_warning_label.setWordWrap(True)
        self.supply_warning_label.hide()
        layout.addWidget(self.supply_warning_label)
        bundle_group = QtWidgets.QGroupBox("2 kg bundle estimate")
        bundle_layout = QtWidgets.QFormLayout(bundle_group)
        self.bundle_load_spin = QtWidgets.QDoubleSpinBox()
        self.bundle_load_spin.setDecimals(3)
        self.bundle_load_spin.setRange(0.0, 2000000.0)
        self.bundle_load_spin.setSuffix(" g / wire")
        self.bundle_load_spin.setSpecialValueText("Set supported load")
        self.bundle_load_spin.setToolTip("Total tension supported by one wire, including its test holder. Bundle estimate excludes extra device/fixture mass and safety margin.")
        self.bundle_label = QtWidgets.QLabel()
        self.bundle_label.setWordWrap(True)
        bundle_layout.addRow("Supported load", self.bundle_load_spin)
        bundle_layout.addRow(self.bundle_label)
        layout.addWidget(bundle_group)
        if pg is not None:
            plot_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
            self.plot = pg.PlotWidget()
            self.plot.setLabel("bottom", "Elapsed", units="s")
            self.plot.setLabel("left", "Current", units="mA")
            self.plot.showGrid(x=True, y=True, alpha=0.2)
            self.target_curve = self.plot.plot(pen=pg.mkPen("#f59e0b", width=2))
            self.measured_curve = self.plot.plot(pen=pg.mkPen("#38bdf8", width=2))
            self.plot.showAxis("right")
            self.plot.setLabel("right", "Resistance", units="Ω", color="#a78bfa")
            self.resistance_view = pg.ViewBox()
            self.plot.scene().addItem(self.resistance_view)
            self.plot.getAxis("right").linkToView(self.resistance_view)
            self.resistance_view.setXLink(self.plot.getViewBox())
            self.resistance_curve = pg.PlotCurveItem(pen=pg.mkPen("#a78bfa", width=2))
            self.resistance_view.addItem(self.resistance_curve)
            self.plot.getViewBox().sigResized.connect(self._sync_resistance_view)
            self._sync_resistance_view()
            plot_splitter.addWidget(self.plot)

            self.resistance_current_plot = pg.PlotWidget()
            self.resistance_current_plot.setLabel("bottom", "Measured current", units="mA")
            self.resistance_current_plot.setLabel("left", "Resistance", units="Ω")
            self.resistance_current_plot.showGrid(x=True, y=True, alpha=0.2)
            self.resistance_current_curve = self.resistance_current_plot.plot(
                pen=pg.mkPen("#a78bfa", width=2),
            )
            self.resistance_current_plot.showAxis("top")
            self.resistance_current_plot.setLabel(
                "top", "Power", units="mW", color="#fb7185"
            )
            self.resistance_current_plot.getViewBox().sigXRangeChanged.connect(
                self._refresh_power_axis
            )
            plot_splitter.addWidget(self.resistance_current_plot)
            plot_splitter.setSizes([420, 300])
            layout.addWidget(plot_splitter, 1)
        else:
            self.plot = None
            self.resistance_view = None
            self.resistance_current_plot = None
            placeholder = QtWidgets.QLabel("Live plot unavailable (pyqtgraph is not installed).")
            placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder, 1)
        controls = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("Start continuous run")
        self.start_button.setMinimumHeight(42)
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setMinimumHeight(42)
        self.stop_button.setEnabled(False)
        self.readout_button = QtWidgets.QPushButton("End sequence → Readout")
        self.readout_button.setMinimumHeight(42)
        self.readout_button.setEnabled(False)
        self.readout_button.setToolTip("Cancel remaining steps/repeats, apply initial current, and keep logging. Stop still turns output off.")
        self.readout_button.clicked.connect(self.end_sequence_readout)
        self.start_button.clicked.connect(self.start_run)
        self.stop_button.clicked.connect(self.stop_run)
        controls.addWidget(self.start_button, 1)
        controls.addWidget(self.readout_button, 1)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        self.path_label = QtWidgets.QLabel("")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        return panel

    def _refresh_bundle_estimate(self) -> None:
        load = self.bundle_load_spin.value()
        if load <= 0:
            self.bundle_label.setText("Enter per-wire supported load to calculate wire count.")
            return
        try:
            peak = max([self.initial_current_spin.value()] + [b.target_mA for b in self.blocks()])
        except (RuntimeError, AttributeError):
            return
        count = math.ceil(2000.0 / load)
        pairs = [(abs(i - peak), i, r) for i, r in zip(self._measured, self._resistance)
                 if math.isfinite(i) and i > 0 and math.isfinite(r) and r > 0]
        if not pairs:
            self.bundle_label.setText(f"{count:,} wires • sequence maximum {peak:g} mA\nPower: awaiting resistance measurements.")
            return
        # Closest current; use the largest R on ties rather than averaging hysteresis branches.
        _, sampled_current, resistance = min(pairs, key=lambda p: (p[0], -p[2]))
        count, per_wire, total = bundle_estimate(load, peak, resistance)
        basis = "displayed history" if self._viewing_history else "retained live history"
        self.bundle_label.setText(
            f"{count:,} wires • sequence maximum {peak:g} mA\n"
            f"{per_wire * 1000:.2f} mW / wire → {total:.2f} W total\n"
            f"Assumed R = {resistance:.1f} Ω from {basis} at {sampled_current:g} mA.\n"
            "I²R estimate; not measured peak power or verified lifting stroke."
        )

    def _sync_resistance_view(self) -> None:
        if self.plot is None or self.resistance_view is None:
            return
        main_view = self.plot.getViewBox()
        self.resistance_view.setGeometry(main_view.sceneBoundingRect())
        self.resistance_view.linkedViewChanged(main_view, pg.ViewBox.XAxis)

    def _sorted_resistance_pairs(self) -> list[tuple[float, float]]:
        return sorted(
            (float(current), float(resistance))
            for current, resistance in zip(self._measured, self._resistance)
            if math.isfinite(float(current))
            and math.isfinite(float(resistance))
            and float(resistance) > 0.0
        )

    def _resistance_at_current(self, current_mA: float, *, pairs=None) -> float | None:
        if pairs is None:
            pairs = self._sorted_resistance_pairs()
        if not pairs:
            return None
        if len(pairs) == 1:
            return pairs[0][1]
        for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
            if x0 <= current_mA <= x1:
                if math.isclose(x0, x1):
                    return y1
                fraction = (current_mA - x0) / (x1 - x0)
                return y0 + fraction * (y1 - y0)
        return min(pairs, key=lambda pair: abs(pair[0] - current_mA))[1]

    def _power_mw_at_current(self, current_mA: float, *, pairs=None) -> float | None:
        resistance = self._resistance_at_current(current_mA, pairs=pairs)
        if resistance is None:
            return None
        return (float(current_mA) ** 2) * resistance / 1000.0

    def _refresh_power_axis(self, *_args: object) -> None:
        if self.resistance_current_plot is None:
            return
        plot_item = self.resistance_current_plot.getPlotItem()
        top_axis = plot_item.getAxis("top")
        x_low, x_high = plot_item.viewRange()[0]
        if not math.isfinite(x_low) or not math.isfinite(x_high) or math.isclose(x_low, x_high):
            return
        bottom_axis = plot_item.getAxis("bottom")
        levels = bottom_axis.tickValues(x_low, x_high, max(1, int(plot_item.getViewBox().width())))
        positions: list[float] = []
        for _spacing, values in levels:
            positions = [
                float(value)
                for value in values
                if math.isfinite(float(value)) and x_low <= float(value) <= x_high
            ]
            if positions:
                break
        if not positions:
            positions = [x_low + (x_high - x_low) * index / 4.0 for index in range(5)]
        ticks = []
        pairs = self._sorted_resistance_pairs()
        for position in positions:
            power = self._power_mw_at_current(position, pairs=pairs)
            if power is None:
                label = ""
            else:
                magnitude = abs(power)
                decimals = 2 if magnitude < 10.0 else (1 if magnitude < 100.0 else 0)
                label = f"{power:.{decimals}f}"
            ticks.append((position, label))
        top_axis.setTicks([ticks])

    def add_block(
        self,
        kind: str,
        *,
        target_mA: float | None = None,
        duration_s: float | None = 10.0,
        label: str = "",
        resistance_ohm: float | None = None,
        resistance_direction: str = "above",
    ) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        number = QtWidgets.QTableWidgetItem(str(row + 1))
        number.setFlags(number.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, number)
        kind_combo = QtWidgets.QComboBox()
        kind_combo.addItems(BLOCK_KINDS)
        kind_combo.setCurrentText(kind)
        kind_combo.currentTextChanged.connect(lambda _text: self._sync_all_block_rows())
        self.table.setCellWidget(row, 1, kind_combo)
        target = QtWidgets.QDoubleSpinBox()
        target.setRange(0.0, 5000.0)
        target.setDecimals(2)
        target.setSuffix(" mA")
        target.setValue(target_mA if target_mA is not None else self.initial_current_spin.value())
        self.table.setCellWidget(row, 2, target)
        duration = QtWidgets.QDoubleSpinBox()
        duration.setRange(0.1, 864000.0)
        duration.setDecimals(2)
        duration.setSuffix(" s")
        duration.setValue(10.0 if duration_s is None else duration_s)
        self.table.setCellWidget(row, 3, duration)
        indefinite = QtWidgets.QCheckBox()
        indefinite.setChecked(duration_s is None)
        indefinite.stateChanged.connect(lambda _state: self._sync_all_block_rows())
        holder = QtWidgets.QWidget()
        holder_layout = QtWidgets.QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        holder_layout.addWidget(indefinite)
        holder.setProperty("check", indefinite)
        self.table.setCellWidget(row, 4, holder)
        self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(label))
        condition = QtWidgets.QComboBox()
        condition.addItem("Time", "time")
        condition.addItem("R ≥ target", "above")
        condition.addItem("R ≤ target", "below")
        condition.setCurrentIndex(condition.findData(resistance_direction if resistance_ohm is not None else "time"))
        condition.setToolTip("Three consecutive valid readings, gaps ≤5 ms. Duration becomes a timeout to readout, not normal advancement.")
        self.table.setCellWidget(row, 6, condition)
        resistance = QtWidgets.QDoubleSpinBox()
        resistance.setRange(.01, 1000000)
        resistance.setDecimals(2)
        resistance.setSuffix(" Ω")
        resistance.setValue(resistance_ohm if resistance_ohm is not None else 170)
        self.table.setCellWidget(row, 7, resistance)
        condition.currentIndexChanged.connect(lambda _: self._sync_all_block_rows())
        self._sync_block_row(row)
        self.table.selectRow(row)

    def _row_check(self, row: int) -> QtWidgets.QCheckBox:
        holder = self.table.cellWidget(row, 4)
        check = holder.property("check") if holder is not None else None
        if not isinstance(check, QtWidgets.QCheckBox):
            raise RuntimeError("Recipe row is missing its indefinite control.")
        return check

    def _sync_block_row(self, row: int) -> None:
        if not 0 <= row < self.table.rowCount():
            return
        kind = self.table.cellWidget(row, 1)
        duration = self.table.cellWidget(row, 3)
        if not isinstance(kind, QtWidgets.QComboBox) or not isinstance(
            duration, QtWidgets.QDoubleSpinBox
        ):
            return
        check = self._row_check(row)
        hold = kind.currentText() == "Hold"
        condition = self.table.cellWidget(row, 6)
        resistance = self.table.cellWidget(row, 7)
        if isinstance(condition, QtWidgets.QComboBox):
            supported = PROFILES[self.mode_combo.currentText()].conditional_steps
            for index in (1, 2):
                item = condition.model().item(index)
                if item is not None:
                    item.setEnabled(supported)
            condition.setToolTip("" if supported else "This supply supports time-based steps only; resistance-step timing is not qualified.")
        target = self.table.cellWidget(row, 2)
        if isinstance(target, QtWidgets.QDoubleSpinBox):
            target.setSingleStep(PROFILES[self.mode_combo.currentText()].current_step)
        conditional = isinstance(condition, QtWidgets.QComboBox) and condition.currentData() != "time"
        if resistance is not None:
            resistance.setEnabled(conditional)
        hold = hold and not conditional
        check.setEnabled(hold)
        if not hold:
            check.setChecked(False)
        duration.setEnabled(not check.isChecked())

    def _sync_all_block_rows(self) -> None:
        for row in range(self.table.rowCount()):
            self._sync_block_row(row)

    def blocks(self) -> list[CurrentBlock]:
        result: list[CurrentBlock] = []
        for row in range(self.table.rowCount()):
            kind = self.table.cellWidget(row, 1)
            target = self.table.cellWidget(row, 2)
            duration = self.table.cellWidget(row, 3)
            if not isinstance(kind, QtWidgets.QComboBox) or not isinstance(
                target, QtWidgets.QDoubleSpinBox
            ) or not isinstance(duration, QtWidgets.QDoubleSpinBox):
                raise RuntimeError(f"Recipe row {row + 1} is incomplete.")
            result.append(
                CurrentBlock(
                    kind.currentText(),
                    target.value(),
                    None if self._row_check(row).isChecked() else duration.value(),
                    self.table.item(row, 5).text() if self.table.item(row, 5) else "",
                    self.table.cellWidget(row, 7).value() if self.table.cellWidget(row, 6).currentData() != "time" else None,
                    self.table.cellWidget(row, 6).currentData() if self.table.cellWidget(row, 6).currentData() != "time" else "above",
                )
            )
        return result

    def set_blocks(self, blocks: Sequence[CurrentBlock]) -> None:
        self.table.setRowCount(0)
        for block in blocks:
            self.add_block(
                block.kind,
                target_mA=block.target_mA,
                duration_s=block.duration_s,
                label=block.label,
                resistance_ohm=block.resistance_ohm,
                resistance_direction=block.resistance_direction,
            )

    def _selected_row(self) -> int:
        indexes = self.table.selectionModel().selectedRows()
        return indexes[0].row() if indexes else -1

    def _duplicate_selected(self) -> None:
        row = self._selected_row()
        if row < 0:
            return
        block = self.blocks()[row]
        self.add_block(block.kind, target_mA=block.target_mA, duration_s=block.duration_s, label=block.label,
                       resistance_ohm=block.resistance_ohm, resistance_direction=block.resistance_direction)

    def _remove_selected(self) -> None:
        row = self._selected_row()
        if row >= 0:
            self.table.removeRow(row)
            self._renumber_rows()

    def _move_selected(self, delta: int) -> None:
        row = self._selected_row()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        blocks = self.blocks()
        blocks[row], blocks[target] = blocks[target], blocks[row]
        self.set_blocks(blocks)
        self.table.selectRow(target)

    def _renumber_rows(self) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setText(str(row + 1))

    def _browse_output(self) -> None:
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Continuous log folder", self.output_edit.text())
        if selected:
            self.output_edit.setText(selected)

    def _show_measurement_history(self) -> None:
        output_dir = Path(self.output_edit.text().strip()).expanduser()
        try:
            entries = find_run_history(output_dir)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Measurement history", str(exc))
            return
        if not entries:
            QtWidgets.QMessageBox.information(
                self,
                "Measurement history",
                f"No saved measurement runs were found in:\n{output_dir}",
            )
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Measurement history")
        dialog.resize(1200, 780)
        layout = QtWidgets.QVBoxLayout(dialog)
        table = QtWidgets.QTableWidget(len(entries), 4)
        table.setHorizontalHeaderLabels(("Run", "Started (UTC)", "Status", "Rows"))
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for row, entry in enumerate(entries):
            name_item = QtWidgets.QTableWidgetItem(entry.run_dir.name)
            name_item.setData(QtCore.Qt.ItemDataRole.UserRole, str(entry.csv_path))
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(entry.started_utc or "—"))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(entry.status))
            table.setItem(
                row,
                3,
                QtWidgets.QTableWidgetItem("—" if entry.rows is None else str(entry.rows)),
            )
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(table)
        preview = HistoryPreview(dialog)
        splitter.addWidget(preview)
        splitter.setSizes([500, 700])
        layout.addWidget(splitter, 1)
        def preview_selection():
            row = table.currentRow()
            if row >= 0:
                preview.select_path(table.item(row,0).data(QtCore.Qt.ItemDataRole.UserRole))
        table.itemSelectionChanged.connect(preview_selection)
        table.selectRow(0)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Open
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        table.doubleClicked.connect(lambda _index: dialog.accept())
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        selected = table.selectedItems()
        if not selected:
            return
        name_item = table.item(selected[0].row(), 0)
        csv_path = Path(str(name_item.data(QtCore.Qt.ItemDataRole.UserRole)))
        try:
            self._load_history_csv(csv_path)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, "Could not load history", str(exc))

    def _load_history_csv(self, csv_path: Path) -> None:
        times: list[float] = []
        targets: list[float] = []
        measured_values: list[float] = []
        resistances: list[float] = []
        with csv_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    elapsed = float(row["elapsed_s"])
                    target = float(row["target_current_mA"])
                except (KeyError, TypeError, ValueError):
                    continue

                def optional_float(name: str) -> float:
                    value = row.get(name)
                    if value in (None, ""):
                        return float("nan")
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return float("nan")

                times.append(elapsed)
                targets.append(target)
                measured_values.append(optional_float("measured_current_mA"))
                resistances.append(optional_float("qualified_resistance_ohm" if row.get("resistance_quality") else "resistance_ohm"))
        if not times:
            raise ValueError(f"No measurement rows were found in {csv_path}")
        self._times = times
        self._targets = targets
        self._measured = measured_values
        self._resistance = resistances
        self._viewing_history = True
        self.elapsed_label.setText(f"{times[-1]:.1f} s")
        self.status_label.setText(f"Viewing history · {csv_path.parent.name}")
        self.block_label.setText("—")
        self.path_label.setText(f"History CSV: {csv_path}")
        self._refresh_plot_data()

    def _export_recipe(self) -> None:
        try:
            blocks = self.blocks()
            repeat_count = self._repeat_count()
            validate_recipe(
                blocks,
                max_current_mA=self.max_current_spin.value(),
                repeat_count=repeat_count,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid program", str(exc))
            return
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export current program", "current-program.json", "JSON (*.json)")
        if filename:
            Path(filename).write_text(
                json.dumps(
                    {
                        "schema": "current_program_recipe_v1",
                        "repeat_count": repeat_count,
                        "blocks": [asdict(b) for b in blocks],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _import_recipe(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import current program", "", "JSON (*.json)")
        if not filename:
            return
        try:
            payload = json.loads(Path(filename).read_text(encoding="utf-8"))
            blocks = [CurrentBlock(**item) for item in payload["blocks"]]
            repeat_count = payload.get("repeat_count", 1)
            if repeat_count is not None:
                repeat_count = int(repeat_count)
            validate_recipe(
                blocks,
                max_current_mA=self.max_current_spin.value(),
                repeat_count=repeat_count,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Could not import program", str(exc))
            return
        self.set_blocks(blocks)
        self._set_repeat_count(repeat_count)

    def _repeat_count(self) -> int | None:
        mode = self.repeat_mode_combo.currentText()
        if mode == "Forever":
            return None
        if mode == "Fixed cycles":
            return self.repeat_count_spin.value()
        return 1

    def _set_repeat_count(self, repeat_count: int | None) -> None:
        if repeat_count is None:
            self.repeat_mode_combo.setCurrentText("Forever")
        elif repeat_count <= 1:
            self.repeat_mode_combo.setCurrentText("Once")
        else:
            self.repeat_mode_combo.setCurrentText("Fixed cycles")
            self.repeat_count_spin.setValue(repeat_count)

    def _refresh_visa_resources(self) -> None:
        mode = self.mode_combo.currentText()
        if self.worker is not None or not PROFILES[mode].vendor:
            return
        if len(self._discovery_jobs) >= 2:
            self.visa_status_label.setText("Previous VISA discovery is still blocked. Enter the resource manually or restart the app after checking the VISA driver.")
            return
        self._discovery_token += 1
        token = self._discovery_token
        self._discovery_busy = True
        self._discovery_requested_text = self.visa_resource_combo.currentText()
        self.visa_status_label.setText("Discovering VISA resources (no output changes)...")
        self.visa_refresh_button.setEnabled(False)
        self.start_button.setEnabled(False)
        job = _VisaDiscoveryJob(token, mode)
        self._discovery_jobs[token] = job
        job.signals.ready.connect(self._on_visa_discovered)
        self._discovery_timer.start()
        QtCore.QThreadPool.globalInstance().start(job)

    def _visa_discovery_timeout(self):
        self._discovery_token += 1
        self._discovery_busy = False
        self.visa_status_label.setText("VISA discovery timed out; no device was opened. Enter the resource manually or retry Refresh.")
        if self.worker is None:
            self.start_button.setEnabled(not self._fault_latched)
            self.visa_refresh_button.setEnabled(bool(PROFILES[self.mode_combo.currentText()].vendor))

    @QtCore.pyqtSlot(int, str, object, str)
    def _on_visa_discovered(self, token, mode, resources, error):
        self._discovery_jobs.pop(token, None)
        if token != self._discovery_token or mode != self.mode_combo.currentText():
            return
        self._discovery_timer.stop()
        self._discovery_busy = False
        if self.worker is not None:
            return
        self.visa_refresh_button.setEnabled(True)
        self.start_button.setEnabled(not self._fault_latched)
        if error:
            self.visa_status_label.setText(f"VISA discovery failed: {error}. Enter a resource or retry Refresh.")
            return
        current = self.visa_resource_combo.currentText().strip()
        # Do not overwrite an address the operator edited during discovery.
        if current != self._discovery_requested_text.strip():
            self.visa_status_label.setText("Discovery complete; preserved your edited resource.")
            return
        vendor = PROFILES[mode].vendor
        matches = [r for r in resources if f"::{vendor}::" in r.upper()]
        if current and current in resources and not current.upper().startswith("USB"):
            matches.insert(0, current)
        self.visa_resource_combo.clear()
        self.visa_resource_combo.addItems(matches)
        if current in matches:
            self.visa_resource_combo.setCurrentText(current)
        elif len(matches) == 1:
            self.visa_resource_combo.setCurrentText(matches[0])
        else:
            self.visa_resource_combo.setCurrentIndex(-1)
            if current and not current.upper().startswith("USB"):
                self.visa_resource_combo.setCurrentText(current)
        if not matches:
            self.visa_status_label.setText("No matching supply found. Check USB, or enter its LAN/VISA resource.")
        elif len(matches) > 1 and current not in matches:
            self.visa_status_label.setText("Multiple matching supplies found; select the intended device.")
        else:
            self.visa_status_label.setText("Matching resource selected. Identity and ownership are verified at Start.")
        editor = self.visa_resource_combo.lineEdit()
        if editor is not None:
            editor.setToolTip(editor.text())
            editor.setCursorPosition(0)

    def _show_status_message(self, message: str) -> None:
        self.status_label.setText(message)

    def _make_adapter(self) -> SupplyAdapter:
        mode = self.mode_combo.currentText()
        if mode == "Siglent SPD1305X (VISA)":
            return SiglentSPD1305XAdapter(
                resource_name=self.visa_resource_combo.currentText(),
                current_limit_mA=self.max_current_spin.value(),
                resource_manager_factory=_default_visa_resource_manager,
                settling_s=self.settling_spin.value(),
            )
        if mode == "Simulation":
            return SimulatedSupplyAdapter()
        if mode == "Keithley 2636B (VISA)":
            return Keithley2636Adapter(
                resource_name=self.visa_resource_combo.currentText(),
                channel=self.keithley_channel_combo.currentText(),
                remote_sense=self.remote_sense_check.isChecked(),
                current_limit_mA=self.max_current_spin.value(),
            )
        return BrokerSupplyAdapter(
            host=self.host_edit.text().strip() or "127.0.0.1",
            port=self.port_spin.value(),
            channel=self.channel_spin.value(),
        )

    def start_run(self) -> None:
        if self._fault_latched:
            self.status_label.setText("Fault latched: inspect connections and acknowledge before a new run")
            return
        if self._discovery_busy:
            self.visa_status_label.setText("Wait for resource discovery before starting.")
            return
        if self.mode_combo.currentText() == "Siglent SPD1305X (VISA)":
            try:
                resource = self.visa_resource_combo.currentText().strip()
                if resource.upper().startswith("USB") and "::0XF4EC::" not in resource.upper():
                    raise ValueError("The selected USB VISA address is not Siglent (0xF4EC). Click Refresh and select the SPD1305X; the old Keithley address cannot be used.")
                blocks = self.blocks()
                values = [self.initial_current_spin.value()] + [b.target_mA for b in blocks]
                if any(0 < value < 1 for value in values):
                    raise ValueError("SPD1305X cannot represent sub-1 mA nonzero setpoints. Use 0 or at least 1 mA.")
                if any(b.resistance_ohm is not None for b in blocks):
                    raise ValueError(
                        "SPD1305X supports time-based steps only for now: resistance-step "
                        "confirmation requires <=5 ms read gaps, but measured I/V query pairs take about 10 ms."
                    )
            except (ValueError, RuntimeError) as exc:
                QtWidgets.QMessageBox.warning(self, "Cannot start Siglent sequence", str(exc))
                return
        if self.worker is not None:
            if self._readout_active and not self._stopping:
                try:
                    self.worker.request_sequence(tuple(self.blocks()), self._repeat_count())
                except ValueError as exc:
                    QtWidgets.QMessageBox.warning(self, "Cannot start sequence", str(exc))
                    return
                self._readout_active = False
                self.start_button.setEnabled(False)
                self.table.setEnabled(False)
            return
        self._stopping = False
        self._readout_active = False
        try:
            blocks = tuple(self.blocks())
            max_current = self.max_current_spin.value()
            repeat_count = self._repeat_count()
            validate_recipe(
                blocks,
                max_current_mA=max_current,
                repeat_count=repeat_count,
            )
            profile = PROFILES[self.mode_combo.currentText()]
            if not profile.conditional_steps and any(b.resistance_ohm is not None for b in blocks):
                raise ValueError("This supply supports time-based steps only; its application rate ceiling cannot satisfy the resistance-step confirmation timing.")
            if self.mode_combo.currentText() == "Siglent SPD1305X (VISA)":
                if not self.visa_resource_combo.currentText().strip():
                    raise ValueError("Select or enter the Siglent VISA resource.")
            if self.mode_combo.currentText() == "Keithley 2636B (VISA)":
                if not self.visa_resource_combo.currentText().strip():
                    raise ValueError("Select or enter the Keithley VISA resource.")
                if max_current > 1500.0:
                    raise ValueError(
                        "Keithley mode is limited to 1500 mA continuous current; "
                        "higher pulse operation needs a separately validated safety envelope."
                    )
            output_text = self.output_edit.text().strip()
            if not output_text:
                raise ValueError("Choose an output folder.")
            output_dir = Path(output_text).expanduser()
            if self.initial_current_spin.value() > max_current:
                raise ValueError("Initial current exceeds the run current limit.")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Cannot start", str(exc))
            return
        config = RunConfig(
            blocks=blocks,
            output_dir=output_dir,
            run_name=self.name_edit.text().strip(),
            control_rate_hz=self.control_rate_spin.value(),
            measurement_rate_hz=self.measurement_rate_spin.value(),
            ui_rate_hz=self.ui_rate_spin.value(),
            initial_current_mA=self.initial_current_spin.value(),
            max_current_mA=max_current,
            voltage_limit_v=self.voltage_spin.value(),
            max_resistance_ohm=(
                self.max_resistance_spin.value()
                if self.resistance_limit_check.isChecked()
                else None
            ),
            repeat_count=repeat_count,
            resistance_action=str(self.resistance_action_combo.currentData()),
            resistance_check_rate_hz=self.resistance_check_rate_spin.value(),
            record_csv=self.record_csv_check.isChecked(),
            log_rate_hz=self.log_rate_spin.value(),
            electrical_limits=ElectricalLimits(
                short_resistance_ohm=self.short_resistance_spin.value() if self.short_check.isChecked() else None,
                open_current_fraction=self.open_fraction_spin.value()/100 if self.open_check.isChecked() else None,
                active_above_mA=self.protection_floor_spin.value(),
                open_confirm_s=self.open_confirm_spin.value(),
            ),
        )
        self._times.clear()
        self._last_qualified_resistance = None
        self._targets.clear()
        self._measured.clear()
        self._resistance.clear()
        self._viewing_history = False
        self.worker = ProgramWorker(config, self._make_adapter(), self)
        self.worker.coalesce_ui = True
        self.worker.sample_ready.connect(self._on_sample)
        self.worker.state_changed.connect(self._on_state)
        self.worker.failed.connect(self._on_failure)
        self.worker.paths_ready.connect(self._on_paths)
        self.worker.finished.connect(self._on_finished)
        self._set_editing_enabled(False)
        self.status_label.setText("Starting…")
        self.worker.start()

    def end_sequence_readout(self) -> None:
        if self.worker is not None and not self._stopping:
            self.worker.request_readout()
            self.readout_button.setEnabled(False)
            self.status_label.setText("Switching to readout…")

    def stop_run(self) -> None:
        if self.worker is not None:
            self._stopping = True
            self.readout_button.setEnabled(False)
            self.start_button.setEnabled(False)
            self.status_label.setText("Stopping safely…")
            self.stop_button.setEnabled(False)
            self.worker.stop()

    def _on_sample(self, row: dict[str, Any]) -> None:
        elapsed = float(row["elapsed_s"])
        self.elapsed_label.setText(f"{elapsed:.1f} s")
        self._times.append(elapsed)
        self._targets.append(float(row["target_current_mA"]))
        measured = row.get("measured_current_mA")
        self._measured.append(float("nan") if measured is None else float(measured))
        resistance = row.get("qualified_resistance_ohm", row.get("resistance_ohm"))
        quality = row.get("resistance_quality", "unfiltered")
        if resistance is not None and math.isfinite(float(resistance)):
            self._last_qualified_resistance = float(resistance)
            self.quality_label.setText(f"Resistance: {float(resistance):.2f} ohm ({quality})")
        elif quality == "settling":
            last = getattr(self, "_last_qualified_resistance", None)
            held = f"Last qualified: {last:.2f} ohm; " if last is not None else ""
            self.quality_label.setText(held + "settling (plot gap; raw data retained)")
        else:
            self.quality_label.setText("Resistance: unavailable")
        self._resistance.append(float("nan") if resistance is None else float(resistance))
        limit = self.live_points_spin.value()
        for values in (self._times, self._targets, self._measured, self._resistance):
            if len(values) > limit:
                del values[:-limit]
        self._refresh_plot_data()

    def _refresh_plot_data(self) -> None:
        if self.plot is not None:
            if self._viewing_history:
                step = max(1, math.ceil(len(self._times) / 20_000))
                data_slice = slice(None, None, step)
            else:
                data_slice = slice(None)
            self.target_curve.setData(self._times[data_slice], self._targets[data_slice])
            self.measured_curve.setData(self._times[data_slice], self._measured[data_slice])
            self.resistance_curve.setData(
                self._times[data_slice], self._resistance[data_slice], connect="finite"
            )
            self.resistance_current_curve.setData(
                self._measured[data_slice], self._resistance[data_slice], connect="finite"
            )
            self._refresh_power_axis()

    def _on_state(self, state: dict[str, Any]) -> None:
        if self.worker is not None:
            self.worker.ui_pending.clear()
        if self._fault_latched:
            return
        self._readout_active = bool(state.get("readout_mode"))
        can_restart = self._readout_active and not state.get("resistance_limit_reached") and not self._stopping
        self.start_button.setText("Start next sequence" if self._readout_active else "Start continuous run")
        self.start_button.setEnabled(can_restart)
        self.table.setEnabled(can_restart)
        self.readout_button.setEnabled(not self._readout_active and not self._stopping)
        cycle = int(state.get("cycle_index", 0)) + 1
        index = int(state["block_index"]) + 1
        label = str(state.get("block_label") or "").strip()
        if state.get("resistance_limit_reached"):
            ceiling = float(state["resistance_current_ceiling_mA"])
            self.status_label.setText(f"Resistance limit reached · current capped at {ceiling:g} mA")
            if state.get("resistance_action") == "readout_current":
                self.status_label.setText(f"Resistance trip · readout {ceiling:g} mA · logging continues")
        elif self._readout_active:
            self.status_label.setText("Readout — waiting for baseline · logging continues")
            if state.get("resistance_timeout"):
                self.status_label.setText("Resistance target timed out — readout · logging continues")
        elif state.get("sequence_complete"):
            self.status_label.setText("Sequence complete · logging continues")
        else:
            self.status_label.setText("Running")
        suffix = f" — {label}" if label else ""
        self.block_label.setText(f"Cycle {cycle} · {index}. {state['block_type']}{suffix}")

    def _on_failure(self, message: str) -> None:
        self._fault_latched = True
        self.start_button.setEnabled(False)
        self.readout_button.setEnabled(False)
        self.status_label.setText("Failed")
        QtWidgets.QMessageBox.critical(self, "Current program failed", message)

    def _reset_fault(self) -> None:
        if self.worker is not None:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Acknowledge fault",
            "Verify the supply output is OFF, inspect wiring and thresholds, and correct the fault. "
            "Allow a new run? This does not turn the output on.",
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._fault_latched = False
            self.reset_fault_button.setEnabled(False)
            self.start_button.setEnabled(True)
            self.status_label.setText("Fault acknowledged; ready for a new run")

    def _on_paths(self, csv_path: str, metadata_path: str) -> None:
        self.path_label.setText(f"CSV: {csv_path or 'disabled (display only)'}\nMetadata: {metadata_path}")

    def _on_finished(self) -> None:
        failed = self.status_label.text() == "Failed"
        self.worker = None
        self._readout_active = False
        self._stopping = False
        self.start_button.setText("Start continuous run")
        self._set_editing_enabled(True)
        if not failed:
            self.status_label.setText("Stopped · output off")

    def _set_editing_enabled(self, enabled: bool) -> None:
        self.start_button.setEnabled(enabled and not self._fault_latched and not self._discovery_busy)
        self.reset_fault_button.setEnabled(enabled and self._fault_latched)
        self.stop_button.setEnabled(not enabled)
        self.readout_button.setEnabled(not enabled)
        self.table.setEnabled(enabled)
        self.import_button.setEnabled(enabled)
        for button in self.recipe_edit_buttons:
            button.setEnabled(enabled)
        for widget in (
            self.short_check, self.short_resistance_spin, self.open_check,
            self.open_fraction_spin, self.protection_floor_spin, self.open_confirm_spin,
            self.record_csv_check, self.log_rate_spin, self.live_points_spin,
            self.mode_combo,
            self.host_edit,
            self.port_spin,
            self.channel_spin,
            self.visa_resource_combo,
            self.visa_refresh_button,
            self.keithley_channel_combo,
            self.remote_sense_check,
            self.voltage_spin,
            self.max_current_spin,
            self.initial_current_spin,
            self.resistance_limit_check,
            self.max_resistance_spin,
            self.resistance_action_combo,
            self.resistance_check_rate_spin,
            self.control_rate_spin,
            self.measurement_rate_spin,
            self.ui_rate_spin,
            self.repeat_mode_combo,
            self.repeat_count_spin,
            self.output_edit,
            self.name_edit,
            self.history_button,
        ):
            widget.setEnabled(enabled)
        self._sync_connection_fields()
        self._sync_repeat_fields()
        self.max_resistance_spin.setEnabled(
            enabled and self.resistance_limit_check.isChecked()
        )

    def _sync_connection_fields(self) -> None:
        mode = self.mode_combo.currentText()
        broker = mode == "Shared HMP broker"
        keithley = mode == "Keithley 2636B (VISA)"
        siglent = mode == "Siglent SPD1305X (VISA)"
        editable = self.worker is None
        self.host_edit.setEnabled(broker and self.worker is None)
        self.port_spin.setEnabled(broker and self.worker is None)
        self.channel_spin.setEnabled(broker and self.worker is None)
        self.visa_resource_combo.setEnabled((keithley or siglent) and editable)
        visa_editor = self.visa_resource_combo.lineEdit()
        if visa_editor is not None:
            visa_editor.setToolTip(visa_editor.text())
            visa_editor.setCursorPosition(0)
        self.visa_refresh_button.setEnabled((keithley or siglent) and editable)
        self.keithley_channel_combo.setEnabled(keithley and editable)
        self.remote_sense_check.setEnabled(keithley and editable)
        self.remote_sense_check.setVisible(not siglent)
        self.connection_form.setRowVisible(self.settling_spin, siglent)
        self.settling_spin.setEnabled(siglent and editable)
        for widget in (self.host_edit, self.port_spin, self.channel_spin):
            self.connection_form.setRowVisible(widget, broker)
        self.connection_form.setRowVisible(self.visa_row, keithley or siglent)
        self.connection_form.setRowVisible(self.keithley_channel_combo, keithley)
        self.connection_form.setRowVisible(self.remote_sense_check, keithley)
        self.visa_status_label.setVisible(keithley or siglent)
        self.visa_refresh_button.setEnabled((keithley or siglent) and editable and not self._discovery_busy)
        if hasattr(self, "supply_warning_label"):
            self.supply_warning_label.setVisible(siglent)
        if keithley:
            self.voltage_spin.setMaximum(20.0)
            self.max_current_spin.setMaximum(1500.0)
        else:
            self.voltage_spin.setMaximum(30.0 if siglent else 32.05)
            self.max_current_spin.setMaximum(5000.0)

    def _sync_repeat_fields(self) -> None:
        self.repeat_count_spin.setEnabled(
            self.worker is None and self.repeat_mode_combo.currentText() == "Fixed cycles"
        )

    # Supply profiles and global settings are deliberately separate.

    def _profile_fields(self):
        return (
            ("broker_host", self.host_edit, "127.0.0.1"), ("broker_port", self.port_spin, 8765),
            ("channel", self.channel_spin, 1), ("visa_resource", self.visa_resource_combo, ""),
            ("keithley_channel", self.keithley_channel_combo, "A"),
            ("keithley_remote_sense", self.remote_sense_check, True),
            ("voltage_limit_v", self.voltage_spin, 1.0), ("max_current_mA", self.max_current_spin, 5.0),
            ("initial_current_mA", self.initial_current_spin, 1.0),
            ("resistance_limit_enabled", self.resistance_limit_check, True),
            ("max_resistance_ohm", self.max_resistance_spin, 190.0),
            ("resistance_action", self.resistance_action_combo, "hold_current"),
            ("control_rate_hz", self.control_rate_spin, 100.0),
            ("measurement_rate_hz", self.measurement_rate_spin, 10.0),
            ("resistance_check_rate_hz", self.resistance_check_rate_spin, 100.0),
            ("ui_rate_hz", self.ui_rate_spin, 10.0),
            ("resistance_settling_s", self.settling_spin, 1.0),
        ) + self._new_settings()

    def _profile_key(self, mode):
        return f"supply_profiles_v1/{PROFILES[mode].key}"

    def _capture_profile(self):
        values = {}
        for key, widget, default in self._profile_fields():
            if isinstance(widget, QtWidgets.QCheckBox): value = widget.isChecked()
            elif isinstance(widget, QtWidgets.QComboBox):
                value = widget.currentData() if widget is self.resistance_action_combo else widget.currentText()
            elif isinstance(widget, QtWidgets.QLineEdit): value = widget.text()
            else: value = widget.value()
            values[key] = value
        values["repeat_count"] = self._repeat_count()
        values["blocks"] = [asdict(b) for b in self.blocks()]
        return values

    def _store_profile(self, mode):
        self.settings.setValue(self._profile_key(mode), json.dumps(self._capture_profile()))

    def _apply_profile(self, mode, values):
        profile = PROFILES[mode]
        defaults = dict(control_rate_hz=profile.control_hz, measurement_rate_hz=profile.acquisition_hz,
                        resistance_check_rate_hz=profile.protection_hz, ui_rate_hz=profile.ui_hz)
        adjusted = []
        self._loading_profile = True
        self._sync_connection_fields()
        self.voltage_spin.setMaximum(profile.max_voltage)
        self.max_current_spin.setMaximum(profile.max_current)
        self.initial_current_spin.setMaximum(profile.max_current)
        self.initial_current_spin.setSingleStep(profile.current_step)
        self.max_current_spin.setSingleStep(profile.current_step)
        self.control_rate_spin.setMaximum(profile.max_control_hz)
        for spin in (self.measurement_rate_spin, self.resistance_check_rate_spin, self.log_rate_spin):
            spin.setMaximum(profile.max_acquisition_hz)
        self.ui_rate_spin.setMaximum(min(60, profile.max_acquisition_hz))
        for key, widget, fallback in self._profile_fields():
            value = values.get(key, defaults.get(key, fallback))
            if isinstance(widget, QtWidgets.QCheckBox):
                widget.setChecked(str(value).lower() not in {"0", "false", "no"})
            elif isinstance(widget, QtWidgets.QComboBox):
                if widget is self.resistance_action_combo:
                    widget.setCurrentIndex(max(0, widget.findData(str(value))))
                else:
                    if widget is self.visa_resource_combo: widget.clear()
                    widget.setCurrentText(str(value))
            elif isinstance(widget, QtWidgets.QLineEdit): widget.setText(str(value))
            else:
                try:
                    number = float(value)
                    if not math.isfinite(number): raise ValueError()
                except (ValueError, TypeError): number = float(defaults.get(key, fallback))
                widget.setValue(int(number) if isinstance(widget, QtWidgets.QSpinBox) else number)
                if widget.value() != number: adjusted.append(key)
        blocks = values.get("blocks", [asdict(CurrentBlock("Hold", 1, 3, "Initial readout")),
                    asdict(CurrentBlock("Ramp", 4, 2, "Low-current test ramp")),
                    asdict(CurrentBlock("Hold", 1, None, "Measure at low current"))])
        try:
            parsed = [CurrentBlock(**item) for item in blocks]
            self.set_blocks(parsed)
            self._set_repeat_count(values.get("repeat_count", 1))
        except (ValueError, TypeError, KeyError):
            self.set_blocks([CurrentBlock("Hold", 1, None)])
            self._set_repeat_count(1)
        self._active_supply_mode = mode
        self._loading_profile = False
        self._sync_connection_fields()
        self._sync_repeat_fields()
        self.profile_label.setText(
            f"{mode}: independent settings. App ceilings: control {profile.max_control_hz:g} Hz, "
            f"read/protection polling {profile.max_acquisition_hz:g} Hz. {profile.rate_basis}"
            + (" Saved values above these ceilings were reduced; review before starting." if adjusted else "")
        )
        self.resistance_check_rate_spin.setToolTip(
            f"Requested polling only; not a guaranteed fresh-measurement rate or fault-response time. {profile.rate_basis}"
        )
        self.measurement_rate_spin.setToolTip(self.resistance_check_rate_spin.toolTip())

    def _read_profile(self, mode):
        try:
            value = json.loads(str(self.settings.value(self._profile_key(mode), "{}")))
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError): return {}

    def _on_supply_changed(self):
        if self._loading_profile or self.worker is not None:
            return
        if self._active_supply_mode is not None:
            self._store_profile(self._active_supply_mode)
        mode = self.mode_combo.currentText()
        self._discovery_token += 1
        self._discovery_timer.stop()
        self._discovery_busy = False
        self._apply_profile(mode, self._read_profile(mode))
        self.visa_status_label.clear()
        self._set_editing_enabled(True)
        if PROFILES[mode].vendor:
            self._refresh_visa_resources()

    def _load_settings(self) -> None:
        self.bundle_load_spin.setValue(float(self.settings.value("bundle_load_g", 0.0)))
        self.output_edit.setText(str(self.settings.value("output_dir", str(Path.home()/"Downloads"))))
        self.name_edit.setText(str(self.settings.value("run_name", "current-program")))
        mode = str(self.settings.value("connection_mode", "Simulation"))
        if mode not in PROFILES: mode = "Simulation"
        self.mode_combo.setCurrentText(mode)
        values = self._read_profile(mode)
        # Migrate old flat settings ONLY into the mode they were last saved for.
        # Other supplies always start with their own defaults. Keep legacy keys untouched.
        if not values:
            for key, widget, default in self._profile_fields():
                value = self.settings.value(key, None)
                if value is not None: values[key] = value
            repeat = self.settings.value("repeat_count", 1)
            values["repeat_count"] = None if str(repeat) == "forever" else int(repeat)
            try: values["blocks"] = json.loads(str(self.settings.value("program_blocks_json", "null")))
            except ValueError: pass
            if values.get("blocks") is None: values.pop("blocks", None)
        self._apply_profile(mode, values)

    def _save_settings(self) -> None:
        self._store_profile(self._active_supply_mode or self.mode_combo.currentText())
        self.settings.setValue("connection_mode", self.mode_combo.currentText())
        self.settings.setValue("output_dir", self.output_edit.text())
        self.settings.setValue("run_name", self.name_edit.text())
        self.settings.setValue("bundle_load_g", self.bundle_load_spin.value())

    def _new_settings(self):
        return (
            ("short_enabled", self.short_check, False),
            ("short_resistance_ohm", self.short_resistance_spin, 10.0),
            ("contact_loss_enabled", self.open_check, False),
            ("contact_loss_percent", self.open_fraction_spin, 25.0),
            ("protection_floor_mA", self.protection_floor_spin, 10.0),
            ("contact_loss_confirm_s", self.open_confirm_spin, 0.2),
            ("record_csv", self.record_csv_check, True),
            ("csv_rate_hz", self.log_rate_spin, 1.0),
            ("live_points", self.live_points_spin, 10_000),
        )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.worker is not None:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Stop current program?",
                "The program is running. Stop it, turn the output off, and close?",
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.stop()
            if not self.worker.wait(5000):
                QtWidgets.QMessageBox.warning(self, "Still stopping", "The hardware worker has not stopped yet.")
                event.ignore()
                return
        self._save_settings()
        event.accept()


WINDOWS: list[CurrentProgramWindow] = []


def main() -> CurrentProgramWindow:
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    ensure_app_theme(app)
    window = CurrentProgramWindow()
    WINDOWS.append(window)
    window.destroyed.connect(lambda: WINDOWS.remove(window) if window in WINDOWS else None)
    window.show()
    if owns_app:
        app.exec()
    return window


def launch() -> None:
    launch_experiment_process(
        ExperimentProcessSpec(
            display_name=APP_TITLE,
            module="experiments.current_program_logger",
            resource_tag=RESOURCE_TAG,
        )
    )


if __name__ == "__main__":
    main()
