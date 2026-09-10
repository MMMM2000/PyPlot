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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from queue import SimpleQueue, Empty
from typing import Any, Callable, Protocol, Sequence

from PyQt6 import QtCore, QtGui, QtWidgets

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
    "power_mW",
    "resistance_limit_reached",
    "resistance_current_ceiling_mA",
    "operating_mode",
    "sequence_id",
    "sequence_elapsed_s",
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

    def __post_init__(self) -> None:
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

    def request_readout(self) -> None:
        self._commands.put(("readout", None))

    def request_sequence(self, blocks: Sequence[CurrentBlock], repeat_count: int | None) -> None:
        validate_recipe(blocks, max_current_mA=self.config.max_current_mA, repeat_count=repeat_count)
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
        sequence_events: list[dict[str, Any]] = []
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            metadata = {
                "schema": "current_program_logger_v1",
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
            }
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            self.paths_ready.emit(str(csv_path), str(metadata_path))
            self.adapter.open()
            opened = True
            self.adapter.configure(
                voltage_v=self.config.voltage_limit_v,
                current_mA=self.config.initial_current_mA,
            )
            warm_up = getattr(self.adapter, "warm_up_measurement", None)
            if callable(warm_up):
                warm_up_started = time.monotonic()
                warm_up()
                metadata["warm_up_measurement_s"] = time.monotonic() - warm_up_started
                metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            if self.config.max_resistance_ohm is not None:
                baseline = self.adapter.measure()
                baseline_current = baseline.get("current_mA")
                baseline_voltage = baseline.get("voltage_V")
                baseline_resistance: float | None = None
                if baseline_current is not None and baseline_voltage is not None:
                    current_mA = float(baseline_current)
                    if abs(current_mA) > 1e-9:
                        baseline_resistance = float(baseline_voltage) / (current_mA / 1000.0)
                metadata["baseline_resistance_ohm"] = baseline_resistance
                if (baseline_current is None or baseline_voltage is None
                        or not math.isfinite(float(baseline_current))
                        or not math.isfinite(float(baseline_voltage))
                        or (self.config.initial_current_mA > 0 and (
                            baseline_resistance is None or not math.isfinite(baseline_resistance)
                            or baseline_resistance <= 0))):
                    raise RuntimeError("Invalid baseline protection measurement; stopping output.")
                if (
                    baseline_resistance is not None
                    and math.isfinite(baseline_resistance)
                    and baseline_resistance >= self.config.max_resistance_ohm
                ):
                    resistance_current_ceiling_mA = self.config.initial_current_mA
                    resistance_limit_reached_elapsed_s = 0.0
                    if self.config.resistance_action == "output_off":
                        self.adapter.close()
                        opened = False
                        self._stop_event.set()
                metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            start = time.monotonic()
            sequence_start = start
            control_interval_s = 1.0 / self.config.control_rate_hz
            measurement_interval_s = 1.0 / self.config.measurement_rate_hz
            acquisition_interval_s = 1.0 / max(
                self.config.measurement_rate_hz,
                self.config.resistance_check_rate_hz if (self.config.max_resistance_ohm is not None
                    or isinstance(engine, ConditionalRecipeEngine)) else 0.0,
            )
            ui_interval_s = 1.0 / self.config.ui_rate_hz
            next_control = start
            next_measurement = start
            next_log = start
            next_ui = start
            state = engine.state_at(0.0)
            target = min(self.config.max_current_mA, max(0.0, state.target_mA))
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                while not self._stop_event.is_set():
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
                            self.config.resistance_check_rate_hz if (self.config.max_resistance_ohm is not None
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
                        readback = self.adapter.measure()
                        acquired_at = time.monotonic()
                        if self.config.max_resistance_ohm is not None:
                            protection_checks += 1
                            if last_check_time is not None:
                                max_check_gap_s = max(max_check_gap_s, acquired_at - last_check_time)
                            last_check_time = acquired_at
                        measured = readback.get("current_mA")
                        voltage = readback.get("voltage_V")
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
                            or (target > 0 and (resistance is None or not math.isfinite(resistance) or resistance <= 0))
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
                            "power_mW": power,
                            "resistance_limit_reached": resistance_current_ceiling_mA is not None,
                            "resistance_current_ceiling_mA": resistance_current_ceiling_mA,
                            "operating_mode": "readout" if readout_mode else "sequence",
                            "sequence_id": sequence_id,
                            "sequence_elapsed_s": now - sequence_start,
                        }
                        if acquired_at >= next_log or tripped_now:
                            writer.writerow(row)
                            handle.flush()
                            rows += 1
                            next_log = acquired_at + measurement_interval_s
                        if now >= next_ui or tripped_now:
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
            self.failed.emit(error)
        finally:
            if opened:
                try:
                    self.adapter.close()
                except Exception as exc:
                    if error is None:
                        error = f"Safe shutdown failed: {exc}"
                        self.failed.emit(error)
            if metadata_path.exists():
                try:
                    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
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
                        sequence_events=sequence_events,
                    )
                    metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                except Exception:
                    pass


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
        self._times: list[float] = []
        self._targets: list[float] = []
        self._measured: list[float] = []
        self._resistance: list[float] = []
        self._viewing_history = False
        self._build_ui()
        self._load_settings()
        install_standard_menu(self, help_topic="logger_current_annealing")

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
        splitter.setSizes([300, 590, 470])

    def _build_setup_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        connection = QtWidgets.QGroupBox("Connection")
        form = QtWidgets.QFormLayout(connection)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Simulation", "Keithley 2636B (VISA)", "Shared HMP broker"])
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
        visa_row.addWidget(self.visa_resource_combo, 1)
        visa_row.addWidget(self.visa_refresh_button)
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
            ("Resistance check rate", self.resistance_check_rate_spin),
            ("Control rate", self.control_rate_spin),
            ("Measurement / log rate", self.measurement_rate_spin),
            ("UI refresh rate", self.ui_rate_spin),
        ):
            form.addRow(label, widget)
        layout.addWidget(connection)

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
        self.mode_combo.currentIndexChanged.connect(self._sync_connection_fields)
        self.repeat_mode_combo.currentIndexChanged.connect(self._sync_repeat_fields)
        self.resistance_limit_check.toggled.connect(self.max_resistance_spin.setEnabled)
        return panel

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

    def _sync_resistance_view(self) -> None:
        if self.plot is None or self.resistance_view is None:
            return
        main_view = self.plot.getViewBox()
        self.resistance_view.setGeometry(main_view.sceneBoundingRect())
        self.resistance_view.linkedViewChanged(main_view, pg.ViewBox.XAxis)

    def _resistance_at_current(self, current_mA: float) -> float | None:
        pairs = sorted(
            (float(current), float(resistance))
            for current, resistance in zip(self._measured, self._resistance)
            if math.isfinite(float(current))
            and math.isfinite(float(resistance))
            and float(resistance) > 0.0
        )
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

    def _power_mw_at_current(self, current_mA: float) -> float | None:
        resistance = self._resistance_at_current(current_mA)
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
        for position in positions:
            power = self._power_mw_at_current(position)
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
                resistances.append(optional_float("resistance_ohm"))
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
        try:
            resources = list_visa_resources()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "VISA discovery failed", str(exc))
            return
        current = self.visa_resource_combo.currentText().strip()
        self.visa_resource_combo.clear()
        self.visa_resource_combo.addItems(resources)
        if current and current not in resources:
            self.visa_resource_combo.addItem(current)
        if current:
            self.visa_resource_combo.setCurrentText(current)
        elif resources:
            preferred = next(
                (resource for resource in resources if "0X05E6" in resource.upper() and "0X2636" in resource.upper()),
                resources[0],
            )
            self.visa_resource_combo.setCurrentText(preferred)
        if not resources:
            self._show_status_message("No VISA resources were found.")

    def _show_status_message(self, message: str) -> None:
        self.status_label.setText(message)

    def _make_adapter(self) -> SupplyAdapter:
        mode = self.mode_combo.currentText()
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
        )
        self._times.clear()
        self._targets.clear()
        self._measured.clear()
        self._resistance.clear()
        self._viewing_history = False
        self.worker = ProgramWorker(config, self._make_adapter(), self)
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
        resistance = row.get("resistance_ohm")
        self._resistance.append(float("nan") if resistance is None else float(resistance))
        self._refresh_plot_data()

    def _refresh_plot_data(self) -> None:
        if self.plot is not None:
            if self._viewing_history:
                step = max(1, math.ceil(len(self._times) / 20_000))
                data_slice = slice(None, None, step)
            else:
                data_slice = slice(max(0, len(self._times) - 5000), None)
            self.target_curve.setData(self._times[data_slice], self._targets[data_slice])
            self.measured_curve.setData(self._times[data_slice], self._measured[data_slice])
            self.resistance_curve.setData(
                self._times[data_slice], self._resistance[data_slice]
            )
            self.resistance_current_curve.setData(
                self._measured[data_slice], self._resistance[data_slice]
            )
            self._refresh_power_axis()

    def _on_state(self, state: dict[str, Any]) -> None:
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
        self.status_label.setText("Failed")
        QtWidgets.QMessageBox.critical(self, "Current program failed", message)

    def _on_paths(self, csv_path: str, metadata_path: str) -> None:
        self.path_label.setText(f"CSV: {csv_path}\nMetadata: {metadata_path}")

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
        self.start_button.setEnabled(enabled)
        self.stop_button.setEnabled(not enabled)
        self.readout_button.setEnabled(not enabled)
        self.table.setEnabled(enabled)
        self.import_button.setEnabled(enabled)
        for button in self.recipe_edit_buttons:
            button.setEnabled(enabled)
        for widget in (
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
        editable = self.worker is None
        self.host_edit.setEnabled(broker and self.worker is None)
        self.port_spin.setEnabled(broker and self.worker is None)
        self.channel_spin.setEnabled(broker and self.worker is None)
        self.visa_resource_combo.setEnabled(keithley and editable)
        visa_editor = self.visa_resource_combo.lineEdit()
        if visa_editor is not None:
            visa_editor.setToolTip(visa_editor.text())
            visa_editor.setCursorPosition(0)
        self.visa_refresh_button.setEnabled(keithley and editable)
        self.keithley_channel_combo.setEnabled(keithley and editable)
        self.remote_sense_check.setEnabled(keithley and editable)
        if keithley:
            self.voltage_spin.setMaximum(20.0)
            self.max_current_spin.setMaximum(1500.0)
        else:
            self.voltage_spin.setMaximum(32.05)
            self.max_current_spin.setMaximum(5000.0)

    def _sync_repeat_fields(self) -> None:
        self.repeat_count_spin.setEnabled(
            self.worker is None and self.repeat_mode_combo.currentText() == "Fixed cycles"
        )

    def _load_settings(self) -> None:
        default_dir = Path.home() / "Downloads"
        self.output_edit.setText(str(self.settings.value("output_dir", str(default_dir))))
        self.output_edit.setCursorPosition(0)
        self.output_edit.setToolTip(self.output_edit.text())
        self.mode_combo.setCurrentText(str(self.settings.value("connection_mode", "Simulation")))
        self.host_edit.setText(str(self.settings.value("broker_host", "127.0.0.1")))
        self.port_spin.setValue(int(self.settings.value("broker_port", 8765)))
        self.channel_spin.setValue(int(self.settings.value("channel", 1)))
        self.visa_resource_combo.setCurrentText(str(self.settings.value("visa_resource", "")))
        visa_editor = self.visa_resource_combo.lineEdit()
        if visa_editor is not None:
            visa_editor.setToolTip(visa_editor.text())
            visa_editor.setCursorPosition(0)
        self.keithley_channel_combo.setCurrentText(str(self.settings.value("keithley_channel", "A")))
        self.remote_sense_check.setChecked(
            str(self.settings.value("keithley_remote_sense", "true")).lower() not in {"0", "false", "no"}
        )
        self.voltage_spin.setValue(float(self.settings.value("voltage_limit_v", self.voltage_spin.value())))
        self.max_current_spin.setValue(
            float(self.settings.value("max_current_mA", self.max_current_spin.value()))
        )
        self.initial_current_spin.setValue(
            float(self.settings.value("initial_current_mA", self.initial_current_spin.value()))
        )
        self.resistance_limit_check.setChecked(
            str(self.settings.value("resistance_limit_enabled", "true")).lower()
            not in {"0", "false", "no"}
        )
        self.max_resistance_spin.setValue(
            float(self.settings.value("max_resistance_ohm", 190.0))
        )
        self.control_rate_spin.setValue(float(self.settings.value("control_rate_hz", 100.0)))
        action_index = self.resistance_action_combo.findData(
            str(self.settings.value("resistance_action", "hold_current"))
        )
        self.resistance_action_combo.setCurrentIndex(max(0, action_index))
        self.resistance_check_rate_spin.setValue(float(self.settings.value("resistance_check_rate_hz", 100.0)))
        self.measurement_rate_spin.setValue(float(self.settings.value("measurement_rate_hz", 10.0)))
        self.ui_rate_spin.setValue(float(self.settings.value("ui_rate_hz", 10.0)))
        self.name_edit.setText(str(self.settings.value("run_name", self.name_edit.text())))
        stored_repeat = self.settings.value("repeat_count", 1)
        self._set_repeat_count(None if str(stored_repeat) == "forever" else int(stored_repeat))
        stored_blocks = self.settings.value("program_blocks_json", "")
        if stored_blocks:
            try:
                blocks = [CurrentBlock(**item) for item in json.loads(str(stored_blocks))]
                validate_recipe(
                    blocks,
                    max_current_mA=5000.0,
                    repeat_count=self._repeat_count(),
                )
                self.set_blocks(blocks)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        self._sync_connection_fields()
        self._sync_repeat_fields()

    def _save_settings(self) -> None:
        self.settings.setValue("output_dir", self.output_edit.text())
        self.settings.setValue("connection_mode", self.mode_combo.currentText())
        self.settings.setValue("broker_host", self.host_edit.text())
        self.settings.setValue("broker_port", self.port_spin.value())
        self.settings.setValue("channel", self.channel_spin.value())
        self.settings.setValue("visa_resource", self.visa_resource_combo.currentText())
        self.settings.setValue("keithley_channel", self.keithley_channel_combo.currentText())
        self.settings.setValue("keithley_remote_sense", self.remote_sense_check.isChecked())
        self.settings.setValue("voltage_limit_v", self.voltage_spin.value())
        self.settings.setValue("max_current_mA", self.max_current_spin.value())
        self.settings.setValue("initial_current_mA", self.initial_current_spin.value())
        self.settings.setValue(
            "resistance_limit_enabled", self.resistance_limit_check.isChecked()
        )
        self.settings.setValue("max_resistance_ohm", self.max_resistance_spin.value())
        self.settings.setValue("resistance_action", self.resistance_action_combo.currentData())
        self.settings.setValue("resistance_check_rate_hz", self.resistance_check_rate_spin.value())
        self.settings.setValue("control_rate_hz", self.control_rate_spin.value())
        self.settings.setValue("measurement_rate_hz", self.measurement_rate_spin.value())
        self.settings.setValue("ui_rate_hz", self.ui_rate_spin.value())
        self.settings.setValue("run_name", self.name_edit.text())
        repeat_count = self._repeat_count()
        self.settings.setValue("repeat_count", "forever" if repeat_count is None else repeat_count)
        self.settings.setValue(
            "program_blocks_json",
            json.dumps([asdict(block) for block in self.blocks()]),
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
