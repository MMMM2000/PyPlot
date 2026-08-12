"""Authoritative Current Annealing run folders and summary generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Iterable


SCHEMA = "current_annealing_session_v2"
MEASUREMENT_FIELDS = (
    "elapsed_s",
    "timestamp_utc",
    "phase",
    "cycle_index",
    "direction",
    "set_current_mA",
    "measured_current_mA",
    "voltage_V",
    "resistance_ohm",
    "power_mW",
    "energy_J",
    "current_density_A_mm2",
    "readback_age_s",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def next_run_directory(parent: Path, base_name: str) -> Path:
    """Return a never-reused ``*_runNN`` directory without creating it."""

    safe = " ".join(str(base_name).strip().split()) or "current_annealing"
    index = 1
    while True:
        candidate = parent / f"{safe}_run{index:02d}"
        if not candidate.exists():
            return candidate
        index += 1


@dataclass(frozen=True, slots=True)
class AnnealingMeasurement:
    elapsed_s: float
    timestamp_utc: str
    phase: str
    cycle_index: int
    direction: str
    set_current_mA: float
    measured_current_mA: float
    voltage_V: float
    resistance_ohm: float
    power_mW: float
    energy_J: float
    current_density_A_mm2: float | None
    readback_age_s: float | None


class CurrentAnnealingSessionWriter:
    """Append-only writer whose folder is authoritative for one run."""

    def __init__(self, run_dir: Path, launch_metadata: dict[str, Any]) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.measurement_path = self.run_dir / "measurement.csv"
        self.metadata_path = self.run_dir / "metadata.json"
        self.recipe_path = self.run_dir / "recipe.json"
        self.log_path = self.run_dir / "run_log.txt"
        self._started_monotonic = time.monotonic()
        self._point_count = 0
        self._last_elapsed_s: float | None = None
        self._last_power_W = 0.0
        self._energy_J = 0.0
        self._metadata = dict(launch_metadata)
        self._metadata.update(
            {
                "schema": SCHEMA,
                "session_state": "running",
                "created_utc": utc_now(),
                "finished_utc": None,
                "duration_s": None,
                "point_count": 0,
                "stop": None,
                "logging": {
                    "measurement_csv": self.measurement_path.name,
                    "run_log": self.log_path.name,
                    "run_summary_png": "run_summary.png",
                    "run_summary_detail_png": "run_summary_detail.png",
                    "run_summary_json": "run_summary.json",
                },
            }
        )
        recipe = dict(self._metadata.get("recipe") or {})
        self.recipe_path.write_text(json.dumps(recipe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.measurement_path.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=MEASUREMENT_FIELDS).writeheader()
        self.log_path.write_text("", encoding="utf-8")
        self._write_metadata()

    @property
    def elapsed_s(self) -> float:
        return max(0.0, time.monotonic() - self._started_monotonic)

    def log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{utc_now()}] {str(message).strip()}\n")

    def append(
        self,
        *,
        phase: str,
        cycle_index: int,
        direction: str,
        set_current_mA: float,
        measured_current_mA: float,
        voltage_V: float,
        diameter_um: float | None,
        readback_age_s: float | None = None,
        elapsed_s: float | None = None,
    ) -> AnnealingMeasurement:
        elapsed = self.elapsed_s if elapsed_s is None else max(0.0, float(elapsed_s))
        measured_A = float(measured_current_mA) / 1000.0
        voltage = float(voltage_V)
        resistance = voltage / measured_A if measured_A > 0.0 else math.nan
        power_W = voltage * measured_A
        if self._last_elapsed_s is not None and elapsed >= self._last_elapsed_s:
            dt = elapsed - self._last_elapsed_s
            self._energy_J += 0.5 * (self._last_power_W + power_W) * dt
        self._last_elapsed_s = elapsed
        self._last_power_W = power_W
        density: float | None = None
        if diameter_um is not None and float(diameter_um) > 0.0:
            area_mm2 = math.pi * ((float(diameter_um) / 1000.0) / 2.0) ** 2
            density = measured_A / area_mm2
        sample = AnnealingMeasurement(
            elapsed_s=elapsed,
            timestamp_utc=utc_now(),
            phase=str(phase),
            cycle_index=max(0, int(cycle_index)),
            direction=str(direction),
            set_current_mA=float(set_current_mA),
            measured_current_mA=float(measured_current_mA),
            voltage_V=voltage,
            resistance_ohm=resistance,
            power_mW=power_W * 1000.0,
            energy_J=self._energy_J,
            current_density_A_mm2=density,
            readback_age_s=None if readback_age_s is None else float(readback_age_s),
        )
        with self.measurement_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MEASUREMENT_FIELDS)
            writer.writerow(asdict(sample))
            handle.flush()
        self._point_count += 1
        return sample

    def finalize(self, *, state: str, reason: str, detail: str = "") -> dict[str, Any]:
        duration = self.elapsed_s
        self._metadata.update(
            {
                "session_state": str(state),
                "finished_utc": utc_now(),
                "duration_s": duration,
                "point_count": self._point_count,
                "stop": {"reason": str(reason), "detail": str(detail)},
            }
        )
        self._write_metadata()
        self.log(f"Session finalized: {state}; {reason}; {detail}".rstrip("; "))
        status_path = self.run_dir / "run_summary_status.json"
        try:
            summary = generate_run_summaries(self.run_dir)
        except Exception as exc:
            # A plotting/backend problem must never invalidate authoritative
            # measurements or turn a completed anneal into a control fault.
            status = {"status": "failed", "error": str(exc), "updated_utc": utc_now()}
            status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            self.log(f"Run summary generation failed: {exc}")
            return status
        status = {"status": "complete", "updated_utc": utc_now(), "summary": summary}
        status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        return summary

    def _write_metadata(self) -> None:
        temporary = self.metadata_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self._metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self.metadata_path)


def _read_measurements(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _numbers(rows: Iterable[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def _read_metadata(run_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resistance_current_floor(metadata: dict[str, Any]) -> tuple[float, float]:
    hardware = metadata.get("hardware")
    hardware = hardware if isinstance(hardware, dict) else {}
    recipe = metadata.get("recipe")
    recipe = recipe if isinstance(recipe, dict) else {}

    def finite_nonnegative(value: Any, fallback: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return fallback
        return number if math.isfinite(number) and number >= 0.0 else fallback

    start_current = finite_nonnegative(
        metadata.get("start_current_mA", recipe.get("start_current_mA")), 0.0
    )
    hardware_minimum = finite_nonnegative(hardware.get("min_positive_current_mA"), 0.0)
    resolution = finite_nonnegative(hardware.get("current_resolution_mA"), 0.0)
    return max(start_current, hardware_minimum), resolution / 2.0


def _valid_resistance_rows(
    rows: Iterable[dict[str, Any]],
    metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], float, float]:
    """Return rows whose resistance is physically supported by a live current readback."""

    current_floor_mA, tolerance_mA = _resistance_current_floor(metadata)
    cutoff_mA = max(0.0, current_floor_mA - tolerance_mA)
    valid: list[dict[str, Any]] = []
    required = ("measured_current_mA", "voltage_V", "resistance_ohm", "power_mW")
    for row in rows:
        try:
            measured_current, voltage, resistance, power = (
                float(row[key]) for key in required
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (measured_current, voltage, resistance, power)):
            continue
        if str(row.get("direction") or "") not in {"heating", "cooling"}:
            continue
        if measured_current < cutoff_mA or voltage <= 0.0 or resistance <= 0.0 or power <= 0.0:
            continue
        valid.append(row)
    return valid, current_floor_mA, tolerance_mA


def _cycle_number(row: dict[str, Any]) -> int:
    try:
        return max(0, int(float(row.get("cycle_index") or 0)))
    except (TypeError, ValueError):
        return 0


def _cycle_series(rows: Iterable[dict[str, Any]]) -> list[tuple[int, str, list[dict[str, Any]]]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        direction = str(row.get("direction") or "").lower()
        cycle = _cycle_number(row)
        if cycle > 0 and direction in {"heating", "cooling"}:
            grouped.setdefault((cycle, direction), []).append(row)
    return [
        (cycle, direction, grouped[(cycle, direction)])
        for cycle, direction in sorted(
            grouped, key=lambda item: (item[0], 0 if item[1] == "heating" else 1)
        )
    ]


def _summary_outcome(metadata: dict[str, Any]) -> dict[str, Any]:
    state = str(metadata.get("session_state") or "unknown").strip().lower()
    stop = metadata.get("stop")
    stop = stop if isinstance(stop, dict) else {}
    reason = str(stop.get("reason") or "").strip()
    detail = str(stop.get("detail") or "").strip()
    combined = f"{reason} {detail}".lower()
    if any(token in combined for token in ("contact", "burn", "wire_broken")):
        kind, label, colour = "contact_lost", "Wire burn-through / electrical contact lost", "#b45309"
    elif state == "completed" or reason.lower() == "recipe_complete":
        kind, label, colour = "completed", "Completed", "#166534"
    elif any(token in reason.lower() for token in ("operator", "cancel", "stopped")):
        kind, label, colour = "operator_stopped", "Stopped by operator", "#475569"
    elif state in {"failed", "faulted", "aborted"}:
        kind, label, colour = "failed", f"Failed: {reason or state}", "#b91c1c"
    else:
        kind, label, colour = state or "unknown", reason or state.title() or "Unknown", "#475569"
    return {
        "kind": kind,
        "label": label,
        "state": state,
        "reason": reason,
        "detail": detail,
        "colour": colour,
        "is_valid_experimental_outcome": kind in {"completed", "contact_lost"},
    }


def _approximate_power_at_currents(
    rows: Iterable[dict[str, Any]], currents_mA: Iterable[float]
) -> list[float | None]:
    """Median nearest measured power per cycle/direction at each current."""

    traces = [trace for _, _, trace in _cycle_series(rows)]
    values: list[float | None] = []
    for current in currents_mA:
        candidates: list[float] = []
        for trace in traces:
            nearest: tuple[float, float] | None = None
            for row in trace:
                try:
                    measured = float(row["measured_current_mA"])
                    power = float(row["power_mW"])
                except (KeyError, TypeError, ValueError):
                    continue
                distance = abs(measured - float(current))
                if math.isfinite(power) and (nearest is None or distance < nearest[0]):
                    nearest = (distance, power)
            if nearest is not None and nearest[0] <= 1.1:
                candidates.append(nearest[1])
        values.append(statistics.median(candidates) if candidates else None)
    return values


def _diameter_um(metadata: dict[str, Any]) -> float | None:
    candidates = [metadata.get("diameter_um")]
    for key in ("sample", "recipe", "microwire_geometry"):
        nested = metadata.get(key)
        if isinstance(nested, dict):
            candidates.append(nested.get("diameter_um"))
    for candidate in candidates:
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            return value
    return None


def generate_run_summaries(run_dir: Path) -> dict[str, Any]:
    """Generate cycle-aware compact/detail summaries without changing raw data."""

    run_dir = Path(run_dir)
    rows = _read_measurements(run_dir / "measurement.csv")
    metadata = _read_metadata(run_dir)
    resistance_rows, current_floor_mA, current_tolerance_mA = _valid_resistance_rows(
        rows, metadata
    )
    directions_by_cycle: dict[int, set[str]] = {}
    for row in rows:
        cycle = _cycle_number(row)
        if cycle:
            directions_by_cycle.setdefault(cycle, set()).add(
                str(row.get("direction") or "").lower()
            )
    elapsed = _numbers(rows, "elapsed_s")
    current = _numbers(rows, "measured_current_mA")
    voltage = _numbers(rows, "voltage_V")
    power = _numbers(rows, "power_mW")
    energy = _numbers(rows, "energy_J")
    density = _numbers(rows, "current_density_A_mm2")
    outcome = _summary_outcome(metadata)
    highest_cycle_index = max(directions_by_cycle, default=0)
    terminal_cycle_completed = (
        str(metadata.get("session_state") or "").strip().lower() == "completed"
        and {"heating", "cooling"}.issubset(
            directions_by_cycle.get(highest_cycle_index, set())
        )
    )
    completed_cycles = max(0, highest_cycle_index - 1)
    if terminal_cycle_completed:
        completed_cycles += 1
    cycle_labels = [
        f"{'Heating' if direction == 'heating' else 'Cooling'} {cycle}"
        for cycle, direction, _ in _cycle_series(resistance_rows)
    ]
    summary = {
        "schema": "current_annealing_run_summary_v2",
        "point_count": len(rows),
        "valid_resistance_point_count": len(resistance_rows),
        "excluded_resistance_point_count": len(rows) - len(resistance_rows),
        "resistance_current_floor_mA": current_floor_mA,
        "resistance_current_tolerance_mA": current_tolerance_mA,
        "duration_s": max(elapsed) if elapsed else 0.0,
        "max_current_mA": max(current) if current else None,
        "max_voltage_V": max(voltage) if voltage else None,
        "max_power_mW": max(power) if power else None,
        "max_current_density_A_mm2": max(density) if density else None,
        "energy_J": max(energy) if energy else 0.0,
        "highest_cycle_index": highest_cycle_index,
        "completed_cycles": completed_cycles,
        "directions": sorted(
            {str(row.get("direction") or "") for row in rows if row.get("direction")}
        ),
        "cycle_series_labels": cycle_labels,
        "approximate_power_axis_method": (
            "median nearest measured power across cycle-direction traces"
        ),
        "outcome": {key: value for key, value in outcome.items() if key != "colour"},
    }
    (run_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not rows:
        return summary

    plot_rows: list[dict[str, Any]] = []
    required = (
        "elapsed_s",
        "set_current_mA",
        "measured_current_mA",
        "voltage_V",
        "power_mW",
    )
    for row in rows:
        try:
            values = [float(row[key]) for key in required]
        except (KeyError, TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in values):
            plot_rows.append(row)
    if not plot_rows:
        return summary

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    def series(key: str, source: list[dict[str, Any]]) -> list[float]:
        return [float(row[key]) for row in source]

    heating_colours = ("#dc2626", "#f97316", "#e11d48", "#ea580c", "#be123c")
    cooling_colours = ("#2563eb", "#0ea5e9", "#4f46e5", "#06b6d4", "#1d4ed8")
    markers = ("o", "s", "D", "^", "v")

    def plot_cycle_lines(axis: Any, y_key: str) -> None:
        for cycle, direction, selected in _cycle_series(resistance_rows):
            palette = heating_colours if direction == "heating" else cooling_colours
            axis.plot(
                series("measured_current_mA", selected),
                series(y_key, selected),
                color=palette[(cycle - 1) % len(palette)],
                marker=markers[(cycle - 1) % len(markers)],
                markersize=2.8,
                linewidth=1.25,
                label=f"{'Heating' if direction == 'heating' else 'Cooling'} {cycle}",
            )

    def full_frame(axis: Any) -> None:
        axis.grid(alpha=0.22)
        for spine in axis.spines.values():
            spine.set_visible(True)

    def add_power_axis(axis: Any) -> None:
        if not resistance_rows:
            return
        low, high = axis.get_xlim()
        ticks = [float(value) for value in axis.get_xticks() if low <= float(value) <= high]
        powers = _approximate_power_at_currents(resistance_rows, ticks)
        top = axis.twiny()
        top.set_xlim(axis.get_xlim())
        top.set_xticks(ticks)
        top.set_xticklabels(
            ["" if value is None else f"≈{value:.0f}" for value in powers]
        )
        top.set_xlabel("Approx. measured power (mW; median across cycles)")
        for spine in top.spines.values():
            spine.set_visible(True)

    density_ratios = [
        float(row["current_density_A_mm2"]) / float(row["measured_current_mA"])
        for row in resistance_rows
        if row.get("current_density_A_mm2") not in {None, ""}
        and float(row["measured_current_mA"]) > 0.0
        and math.isfinite(float(row["current_density_A_mm2"]))
    ]
    density_ratio = statistics.median(density_ratios) if density_ratios else None

    def add_density_axis(axis: Any) -> None:
        if density_ratio is None or not math.isfinite(density_ratio) or density_ratio <= 0.0:
            return
        top = axis.secondary_xaxis(
            "top",
            functions=(
                lambda value: value * density_ratio,
                lambda value: value / density_ratio,
            ),
        )
        diameter = _diameter_um(metadata)
        suffix = f", d = {diameter:g} µm" if diameter is not None else ""
        top.set_xlabel(f"Current density (A/mm²{suffix})")

    def add_cycle_legend(fig: Any, axis: Any, y: float) -> None:
        handles, labels = axis.get_legend_handles_labels()
        if handles:
            fig.legend(
                handles,
                labels,
                loc="upper center",
                ncol=min(4, len(labels)),
                frameon=False,
                bbox_to_anchor=(0.5, y),
            )

    max_power = summary["max_power_mW"] or 0.0
    max_current = summary["max_current_mA"] or 0.0
    footer = (
        f"{summary['completed_cycles']} completed cycle(s) | {summary['duration_s']:.1f} s | "
        f"max {max_current:.1f} mA | max power {max_power:.0f} mW | "
        f"energy {summary['energy_J']:.1f} J | {len(resistance_rows)} derived-plot points "
        f"({len(rows) - len(resistance_rows)} startup/invalid excluded; raw CSV preserved)"
    )

    fig, axes = plt.subplots(1, 2, figsize=(10, 5.8))
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.20, top=0.70, wspace=0.30)
    derived_plot_left_mA = max(0.0, current_floor_mA - current_tolerance_mA)
    plot_cycle_lines(axes[0], "resistance_ohm")
    axes[0].set_xlim(left=derived_plot_left_mA)
    axes[0].set(
        xlabel="Measured current (mA)",
        ylabel="Resistance (Ohm)",
        title="",
    )
    add_power_axis(axes[0])
    plot_cycle_lines(axes[1], "power_mW")
    axes[1].set_xlim(left=derived_plot_left_mA)
    axes[1].set(
        xlabel="Measured current (mA)",
        ylabel="Measured power (mW)",
        title="",
    )
    add_density_axis(axes[1])
    for axis in axes:
        full_frame(axis)
    axes[0].text(
        0.5, 0.94, "Resistance vs current", transform=axes[0].transAxes,
        ha="center", fontsize=12
    )
    axes[1].text(
        0.5, 0.94, "Power vs current", transform=axes[1].transAxes,
        ha="center", fontsize=12
    )
    if cycle_labels:
        axes[0].legend(
            loc="upper left", bbox_to_anchor=(0.01, 0.87), ncol=2,
            frameon=True, framealpha=0.88, fontsize=8
        )
    fig.suptitle(
        f"{run_dir.name}\n{outcome['label']}", fontsize=12, color=outcome["colour"]
    )
    fig.text(0.5, 0.025, footer, ha="center", fontsize=7.5)
    fig.savefig(run_dir / "run_summary.png", dpi=170)
    plt.close(fig)

    fig, axes = plt.subplots(3, 2, figsize=(12, 12))
    fig.subplots_adjust(
        left=0.075, right=0.975, bottom=0.075, top=0.86, hspace=0.64, wspace=0.27
    )
    time_values = series("elapsed_s", plot_rows)
    axes[0, 0].plot(time_values, series("set_current_mA", plot_rows), label="Set")
    axes[0, 0].plot(
        time_values,
        series("measured_current_mA", plot_rows),
        label="Measured",
        alpha=0.8,
    )
    axes[0, 0].set(
        xlabel="Elapsed time (s)", ylabel="Current (mA)", title="Current vs time"
    )
    axes[0, 0].legend(frameon=False)
    axes[0, 1].plot(
        series("elapsed_s", resistance_rows),
        series("resistance_ohm", resistance_rows),
        color="#14b8a6",
    )
    axes[0, 1].set(
        xlabel="Elapsed time (s)", ylabel="Resistance (Ohm)", title="Resistance vs time"
    )
    axes[1, 0].plot(time_values, series("voltage_V", plot_rows), color="#9333ea")
    axes[1, 0].set(
        xlabel="Elapsed time (s)", ylabel="Voltage (V)", title="Voltage vs time"
    )
    axes[1, 1].plot(time_values, series("power_mW", plot_rows), color="#f59e0b")
    axes[1, 1].set(
        xlabel="Elapsed time (s)", ylabel="Power (mW)", title="Measured power vs time"
    )
    plot_cycle_lines(axes[2, 0], "resistance_ohm")
    axes[2, 0].set_xlim(left=derived_plot_left_mA)
    axes[2, 0].set(
        xlabel="Measured current (mA)",
        ylabel="Resistance (Ohm)",
        title="",
    )
    add_power_axis(axes[2, 0])
    plot_cycle_lines(axes[2, 1], "power_mW")
    axes[2, 1].set_xlim(left=derived_plot_left_mA)
    axes[2, 1].set(
        xlabel="Measured current (mA)",
        ylabel="Measured power (mW)",
        title="",
    )
    add_density_axis(axes[2, 1])
    if outcome["kind"] == "contact_lost" and resistance_rows:
        terminal_time = float(resistance_rows[-1]["elapsed_s"])
        terminal_current = float(resistance_rows[-1]["measured_current_mA"])
        for axis in axes[:2, :].flat:
            axis.axvline(
                terminal_time, color=outcome["colour"], linestyle="--", linewidth=1.2
            )
        for axis in axes[2, :]:
            axis.axvline(
                terminal_current,
                color=outcome["colour"],
                linestyle="--",
                linewidth=1.2,
            )
    for axis in axes.flat:
        full_frame(axis)
    axes[2, 0].text(
        0.5, 1.32, "Resistance vs current", transform=axes[2, 0].transAxes,
        ha="center", fontsize=11
    )
    axes[2, 1].text(
        0.5, 1.32, "Power vs current", transform=axes[2, 1].transAxes,
        ha="center", fontsize=11
    )
    add_cycle_legend(fig, axes[2, 0], 0.91)
    fig.suptitle(f"{run_dir.name}\n{outcome['label']}", color=outcome["colour"])
    fig.text(
        0.5,
        0.015,
        f"{len(rows)} raw samples | {len(resistance_rows)} valid derived-plot samples | "
        f"{len(rows) - len(resistance_rows)} startup/invalid excluded only from derived plots",
        ha="center",
        fontsize=8,
    )
    fig.savefig(run_dir / "run_summary_detail.png", dpi=170)
    plt.close(fig)
    return summary
