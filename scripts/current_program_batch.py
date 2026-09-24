"""Finite Keithley hold/cool trials using the Current Program Logger worker.

Dry-run is the default. Live use requires --live and an exact VISA resource.
Every trial opens and closes the output separately; faults stop the batch.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.current_program_logger import (  # noqa: E402
    CurrentBlock, Keithley2636Adapter, ProgramWorker, RunConfig,
    _default_visa_resource_manager,
)
from experiments.current_program_support import ElectricalLimits  # noqa: E402


class OwnedKeithley2636Adapter(Keithley2636Adapter):
    """Batch-only ownership: exclusive lock, output-off preflight and verified cleanup."""

    def open(self) -> None:
        super().open()
        device = self._device()
        self._locked = False
        try:
            device.lock_excl(timeout=1000)
            self._locked = True
            output = float(device.query(f"print({self._smu}.source.output)"))
            off = float(device.query(f"print({self._smu}.OUTPUT_OFF)"))
            if not math.isfinite(output) or output != off:
                raise RuntimeError("Keithley channel output is already on; batch refuses takeover.")
        except BaseException:
            # No configuration commands have been issued; do not alter another owner.
            self._release_without_commands()
            raise

    def _release_without_commands(self) -> None:
        device, manager = self._instrument, self._manager
        self._instrument = self._manager = None
        try:
            if device is not None:
                try:
                    if getattr(self, "_locked", False):
                        device.unlock()
                finally:
                    device.close()
        finally:
            self._locked = False
            if manager is not None:
                manager.close()

    def close(self) -> None:
        device = self._instrument
        errors = []
        if device is not None:
            for command in (
                f"{self._smu}.source.output = {self._smu}.OUTPUT_OFF",
                f"{self._smu}.source.leveli = 0",
            ):
                try:
                    device.write(command)
                except Exception as exc:
                    errors.append(f"{command}: {exc}")
            try:
                output = float(device.query(f"print({self._smu}.source.output)"))
                off = float(device.query(f"print({self._smu}.OUTPUT_OFF)"))
                level = float(device.query(f"print({self._smu}.source.leveli)"))
                if output != off or not math.isclose(level, 0, abs_tol=1e-12):
                    errors.append(f"output={output!r}, level={level!r}; safe state not confirmed")
            except Exception as exc:
                errors.append(f"safe-state readback failed: {exc}")
        try:
            self._release_without_commands()
        except Exception as exc:
            errors.append(f"VISA release failed: {exc}")
        if errors:
            raise RuntimeError("Keithley batch shutdown: " + "; ".join(errors))


def running_controllers() -> list[str]:
    """Conservatively refuse another logger or launcher process."""
    found = []
    own = psutil.Process().pid
    for process in psutil.process_iter(("pid", "name", "cmdline")):
        try:
            if process.info["pid"] == own:
                continue
            command = " ".join(process.info.get("cmdline") or []).lower()
            if "experiments.current_program_logger" in command or "launcher.py" in command:
                found.append(f"PID {process.info['pid']}: {command[:160]}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Inability to inspect another Python process is an ownership failure.
            if (process.info.get("name") or "").lower() in {"python.exe", "pythonw.exe"}:
                found.append(f"PID {process.info['pid']}: inaccessible Python controller")
    return found


def trial_config(output_dir: Path, run_name: str, current_mA: int) -> RunConfig:
    if type(current_mA) is not int or not 2 <= current_mA <= 10:
        raise ValueError("This batch permits only integer 2-10 mA trial levels.")
    return RunConfig(
        blocks=(
            CurrentBlock("Hold", float(current_mA), 600.0,
                         "Heat until 180 ohm or 600 s", 180.0, "above"),
            CurrentBlock("Hold", 0.1, 30.0, "Cooling"),
        ),
        output_dir=output_dir,
        run_name=f"{run_name}-batch-{current_mA:02d}mA",
        control_rate_hz=100.0,
        measurement_rate_hz=20.0,
        ui_rate_hz=2.0,
        initial_current_mA=0.1,
        max_current_mA=10.0,
        voltage_limit_v=3.0,
        repeat_count=1,
        resistance_check_rate_hz=1000.0,
        record_csv=True,
        log_rate_hz=10.0,
        electrical_limits=ElectricalLimits(
            short_resistance_ohm=10.0,
            open_current_fraction=0.25,
            active_above_mA=1.0,
            open_confirm_s=0.25,
        ),
        advance_on_resistance_timeout=True,
        stop_when_sequence_complete=True,
    )


def latest_trial_metadata(output_dir: Path, run_name: str) -> tuple[Path, dict]:
    choices = sorted(output_dir.glob(f"{run_name}_*/metadata.json"),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    if not choices:
        raise RuntimeError(f"No saved metadata found for {run_name}")
    path = choices[0]
    return path, json.loads(path.read_text(encoding="utf-8"))


def run_batch(*, resource: str, output_dir: Path, run_name: str,
              first: int = 2, last: int = 10, live: bool = False) -> dict:
    if not 2 <= first <= last <= 10:
        raise ValueError("Batch range must stay within 2-10 mA.")
    if not resource.upper().startswith("USB0::0X05E6::0X2636::"):
        raise ValueError("Specify the verified Keithley 2636B USB VISA resource.")
    configs = [trial_config(output_dir, run_name, level) for level in range(first, last + 1)]
    preview = dict(resource=resource, levels_mA=list(range(first, last + 1)),
                   output_dir=str(output_dir), hold_limit_s=600, cooling_s=30,
                   resistance_target_ohm=180, current_ceiling_mA=10,
                   voltage_ceiling_v=3, max_total_s=len(configs) * 630)
    if not live:
        return dict(preview=preview, status="dry_run")
    if running_controllers():
        raise RuntimeError("Other logger/launcher processes are active: " +
                           "; ".join(running_controllers()))
    if not output_dir.is_dir():
        raise ValueError(f"Output directory is unavailable: {output_dir}")
    batch_dir = Path(__file__).resolve().parents[1] / "artifacts" / "current-program-batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = batch_dir / ("batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    manifest = dict(preview=preview, status="running", trials=[],
                    started_utc=datetime.now(timezone.utc).isoformat())

    def save() -> None:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    save()
    try:
        for config in configs:
            if running_controllers():
                raise RuntimeError("Another controller appeared between trials; batch stopped.")
            adapter = OwnedKeithley2636Adapter(
                resource_name=resource, channel="A", remote_sense=False,
                current_limit_mA=10.0,
                resource_manager_factory=_default_visa_resource_manager,
            )
            worker = ProgramWorker(config, adapter)
            print(f"Starting {config.blocks[0].target_mA:g} mA: {config.run_name}", flush=True)
            worker.start()
            try:
                deadline = time.monotonic() + 650.0
                while not worker.wait(1000):
                    if time.monotonic() > deadline:
                        worker.stop()
                        raise TimeoutError("Trial exceeded bounded 650 s wall time")
            except BaseException:
                worker.stop()
                worker.wait(15000)
                raise
            path, metadata = latest_trial_metadata(output_dir, config.run_name)
            events = metadata.get("sequence_events", [])
            record = dict(current_mA=config.blocks[0].target_mA,
                          metadata_path=str(path), status=metadata.get("status"),
                          error=metadata.get("error"), events=events,
                          rows=metadata.get("rows"))
            manifest["trials"].append(record)
            save()
            if metadata.get("status") != "stopped" or metadata.get("electrical_fault"):
                raise RuntimeError(f"Trial failed at {record['current_mA']:g} mA: {record['error']}")
            if not any(event.get("event") == "sequence_complete_stop" for event in events):
                raise RuntimeError("Trial did not finish cooling and confirm an automatic stop")
            if not any(event.get("event") in {"resistance_target", "resistance_timeout_continue"} for event in events):
                raise RuntimeError("Trial has no confirmed heating outcome")
            print(f"Completed {record['current_mA']:g} mA; output off; rows={record['rows']}", flush=True)
        manifest["status"] = "complete"
    except BaseException as exc:
        manifest["status"] = "stopped_on_error"
        manifest["error"] = repr(exc)
        raise
    finally:
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print(f"Manifest: {manifest_path}", flush=True)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--first", type=int, default=2)
    parser.add_argument("--last", type=int, default=10)
    parser.add_argument("--live", action="store_true", help="Energize the wire; otherwise preview only")
    args = parser.parse_args()
    result = run_batch(resource=args.resource, output_dir=args.output_dir,
                       run_name=args.run_name, first=args.first, last=args.last,
                       live=args.live)
    if not args.live:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
