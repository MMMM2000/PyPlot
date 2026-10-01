"""Finite phone-video pulse/hold/cool screen. Preview only unless --live is given."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6 import QtCore, QtGui, QtWidgets
from experiments.current_program_logger import CurrentBlock, ProgramWorker, RunConfig
from experiments.current_program_support import BufferedCsv, ColdResetLimits, ElectricalLimits
from scripts.current_program_batch import OwnedKeithley2636Adapter, latest_trial_metadata, running_controllers
from plotting.shared.power_guard import create_experiment_sleep_guard

HOLD_LEVELS = (3.0, 2.5, 2.0, 3.0)
RESOURCE = "USB0::0x05E6::0x2636::4093243::INSTR"


def hold_config(output_dir, run_name, hold_mA, *, reference=None):
    if hold_mA is not None and (not math.isfinite(hold_mA) or not 2 <= hold_mA <= 3):
        raise ValueError("This screen only permits 2-3 mA holds, or a no-hold reference.")
    blocks = [CurrentBlock("Hold", .1, 3, "Cold reference"),
              CurrentBlock("Hold", 10, .5, "Heating pulse", 185, "above")]
    if hold_mA is not None:
        blocks.append(CurrentBlock("Hold", hold_mA, 10, "Low-power hold"))
    blocks.append(CurrentBlock("Hold", .1, 20, "Cooling / cold reset"))
    last = len(blocks)-1
    return RunConfig(
        blocks=tuple(blocks), output_dir=Path(output_dir), run_name=run_name,
        control_rate_hz=500, measurement_rate_hz=1000, ui_rate_hz=2,
        initial_current_mA=.1, max_current_mA=10, voltage_limit_v=3,
        max_resistance_ohm=190, max_resistance_active_above_mA=1,
        resistance_action="readout_current", resistance_check_rate_hz=1000,
        emergency_resistance_ohm=195,
        log_rate_hz=1000, log_rate_schedule=((last, 2, 100),),
        electrical_limits=ElectricalLimits(10, .25, 1, .25),
        advance_on_resistance_timeout=True, stop_when_sequence_complete=True,
        cold_reset=ColdResetLimits(reference_ohm=reference, tolerance_ohm=5),
    )


class SyncBoard(QtWidgets.QWidget):
    """Optical cue on the same monotonic clock as the local controller.

    Paint completion is NOT a measured screen photon timestamp. Preserve this
    uncertainty (display refresh/latency, rolling shutter) during video alignment.
    """
    def __init__(self, path):
        super().__init__()
        self.setWindowTitle("Experiment video sync — include this in the phone view")
        self.resize(680, 320)
        self.label = "READY — no output commands"
        self.stop_requested = False
        self.origin = time.monotonic()
        self.sync_id = path.stem[-8:]
        self.last_code = None
        self.writer = BufferedCsv(path, ("timestamp_utc", "monotonic_s", "cue_code", "label"))
        self.writer.start()
        self.sleep_guard = create_experiment_sleep_guard("Phone video sync board", keep_display_awake=True)
        try:
            self.sleep_guard.acquire()
        except BaseException:
            self.writer.close()
            raise
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.update)
        self.timer.start()

    def paintEvent(self, event):
        now = time.monotonic()
        code = int((now-self.origin)*4)
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("black"))
        painter.setPen(QtGui.QColor("white"))
        painter.setFont(QtGui.QFont("Consolas", 19))
        painter.drawText(20, 45, self.label)
        painter.setFont(QtGui.QFont("Consolas", 34))
        painter.drawText(20, 110, f"CUE {code:06d}")
        painter.drawText(20, 175, f"PC {now:.3f} s")
        for bit in range(12):
            color = "white" if (code >> bit) & 1 else "#222222"
            painter.fillRect(20+bit*50, 200, 44, 70, QtGui.QColor(color))
        painter.setFont(QtGui.QFont("Consolas", 14))
        painter.drawText(20, 305, f"SYNC SESSION {self.sync_id}")
        painter.end()
        if code != self.last_code:
            try:
                self.writer.submit(dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    monotonic_s=now, cue_code=code, label=self.label))
                self.last_code = code
            except Exception:
                self.stop_requested = True
                self.label = "SYNC LOG FAILED — STOP REQUESTED"

    def closeEvent(self, event):
        self.stop_requested = True
        self.timer.stop()
        self.sleep_guard.release()
        event.accept()


def run_screen(*, resource, output_dir, run_name, pilot=False, live=False,
               board=None, process_events=lambda: None, manifest_dir=None):
    if not resource.upper().startswith("USB0::0X05E6::0X2636::"):
        raise ValueError("Specify the Keithley 2636B USB VISA resource.")
    levels = (None,) if pilot else HOLD_LEVELS
    configs = [hold_config(output_dir, f"{run_name}-hold-{i+1:02d}", level)
               for i, level in enumerate(levels)]
    preview = dict(resource=resource, channel="A", remote_sense=False,
        hold_levels_mA=levels, configs=[asdict(config) for config in configs],
        pilot=pilot, max_wall_time_per_trial_s=115,
        timing="Host-timed pulse; verify actual command spacing in the pilot. Not a hardware interlock.")
    if not live:
        return dict(status="dry_run", preview=preview)
    if not Path(output_dir).is_dir():
        raise ValueError("The requested output folder is unavailable.")
    conflicts = running_controllers()
    if conflicts:
        raise RuntimeError("Close other controllers first: " + "; ".join(conflicts))
    batch_dir = (Path(manifest_dir) if manifest_dir is not None else
                 Path(__file__).resolve().parents[1]/"artifacts"/"current-program-batches")
    batch_dir.mkdir(parents=True, exist_ok=True)
    path = batch_dir / f"hold-batch-{uuid.uuid4().hex}.json"
    manifest = dict(status="running", started_utc=datetime.now(timezone.utc).isoformat(),
                    pid=os.getpid(), preview=preview, trials=[],
                    sync_log=str(board.writer.path) if board else None)
    reference = None

    def save():
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    save()
    try:
        for index, level in enumerate(levels):
            if running_controllers():
                raise RuntimeError("Another controller appeared; refusing the next trial.")
            if board and board.stop_requested:
                raise RuntimeError("Sync window closed or failed; batch cancelled.")
            config = hold_config(output_dir, configs[index].run_name, level, reference=reference)
            adapter = OwnedKeithley2636Adapter(resource_name=resource, channel="A",
                                               remote_sense=False, current_limit_mA=10)
            worker = ProgramWorker(config, adapter)
            manifest["active_trial"] = dict(run_name=config.run_name, hold_current_mA=level,
                                             started_utc=datetime.now(timezone.utc).isoformat())
            save()
            if board:
                board.label = f"TRIAL {index+1} / {len(levels)} — {level if level else 'REFERENCE'} mA"
            worker.start()
            deadline = time.monotonic()+115
            try:
                while not worker.wait(20):
                    process_events()
                    if board and board.stop_requested:
                        raise RuntimeError("Sync window closed or failed; stop requested.")
                    if time.monotonic() > deadline:
                        raise TimeoutError("Trial exceeded 115 seconds wall time.")
            except BaseException:
                worker.stop()
                if not worker.wait(15000):
                    raise RuntimeError("Controller did not exit; output state is UNKNOWN.")
                try:
                    interrupted_path, interrupted = latest_trial_metadata(Path(output_dir), config.run_name)
                    manifest["interrupted_trial"] = dict(metadata_path=str(interrupted_path),
                        status=interrupted.get("status"), error=interrupted.get("error"),
                        output_off_verified=interrupted.get("output_off_verified"))
                except Exception:
                    manifest["interrupted_trial"] = dict(output_off_verified=None,
                        error="Saved shutdown metadata unavailable; output state must be checked.")
                raise
            metadata_path, data = latest_trial_metadata(Path(output_dir), config.run_name)
            manifest["trials"].append(dict(hold_current_mA=level, metadata_path=str(metadata_path),
                status=data.get("status"), error=data.get("error"),
                output_off_verified=data.get("output_off_verified"),
                guard_interrupted=data.get("resistance_limit_reached"), rows=data.get("rows")))
            manifest["active_trial"] = None
            save()
            if (data.get("status") != "stopped" or not data.get("output_off_verified")
                    or not data.get("cold_reset_verified")):
                raise RuntimeError(f"Trial failed or safe/reset state unconfirmed: {data.get('error')}")
            # Preserve the FIRST cold reference; do not ratchet it upward between trials.
            if reference is None:
                reference = data["cold_reference_ohm"]
            commands = data.get("current_commands", [])
            heat = next((i for i, c in enumerate(commands) if c["target_current_mA"] == 10), None)
            if heat is None or heat+1 >= len(commands):
                raise RuntimeError("Missing pulse command/termination timestamps.")
            width = commands[heat+1]["elapsed_s"] - commands[heat]["issue_elapsed_s"]
            manifest["trials"][-1]["pulse_command_interval_s"] = width
            if width > .55:
                raise RuntimeError("Pulse command interval exceeded 0.55 s; stop and review timing.")
            save()
        manifest["status"] = "complete"
        if board:
            board.label = "COMPLETE — OUTPUT OFF VERIFIED"
    except BaseException as exc:
        manifest.update(status="stopped_on_error", error=repr(exc))
        if board:
            board.label = "STOPPED — CHECK MANIFEST"
        raise
    finally:
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print(f"Manifest: {path}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--pilot", action="store_true", help="One no-hold reference, not the four-trial screen")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--pilot-reviewed", action="store_true",
                        help="Required for a live screen: pulse timing/data and visible stroke were reviewed")
    parser.add_argument("--sync-board", action="store_true", help="Visible PC timing cue; closing it cancels a run")
    args = parser.parse_args()
    if args.live and not args.pilot and not args.pilot_reviewed:
        parser.error("Run/review the no-hold --pilot first; then explicitly pass --pilot-reviewed.")
    app = board = None
    if args.sync_board:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        directory = Path(__file__).resolve().parents[1]/"artifacts"/"current-program-batches"
        directory.mkdir(parents=True, exist_ok=True)
        board = SyncBoard(directory/f"video-sync-{uuid.uuid4().hex}.csv")
        board.show()
        app.processEvents()
    try:
        result = run_screen(resource=args.resource, output_dir=args.output_dir, run_name=args.run_name,
                            pilot=args.pilot, live=args.live, board=board,
                            process_events=app.processEvents if app else lambda: None)
        print(json.dumps(result, indent=2, default=str))
        if app:
            # Keep the terminal cue visible in the recording; no instrument is owned here.
            app.exec()
    finally:
        if board:
            board.close()
            board.writer.close()


if __name__ == "__main__":
    main()
