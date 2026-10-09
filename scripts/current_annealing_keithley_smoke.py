"""Isolated, finite CA logger pilot. --live requires an explicit USB resource.

Only a 1 -> 2 -> 1 mA triangle is permitted; 1 V compliance, output OFF at end.
No real app settings or user experiment files are read/written.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil
from PyQt6 import QtCore, QtWidgets

from data_logging.current_annealing_logger import current_annealing_logger as ca
from data_logging.current_annealing_logger.keithley import AnnealingKeithley
from experiments.current_program_logger import _default_visa_resource_manager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--resource", default="")
    args = parser.parse_args()
    if not args.live:
        print("Dry run: 1 to 2 to 1 mA, 0.5 mA/s, 1 V compliance; 15 s timeout.")
        return
    if not args.resource.upper().startswith("USB0::0X05E6::0X2636::"):
        raise ValueError("Specify the connected Keithley 2636 USB resource.")
    own = psutil.Process()
    exempt = {own.pid, *(p.pid for p in own.parents())}
    for process in psutil.process_iter(["name", "cmdline"]):
        if process.pid in exempt:
            continue
        command = " ".join(process.info["cmdline"] or []).lower()
        if any(token in command for token in ("launcher.py", "current_program", "current_annealing", "mini_dma", "tma_logger")):
            raise RuntimeError(f"Another possible controller is open (PID {process.pid}); close it first.")

    root = Path(__file__).resolve().parents[1] / "artifacts" / "keithley-ca-pilot" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root.mkdir(parents=True)
    settings_type = QtCore.QSettings
    ca.QtCore.QSettings = lambda organization="", application="": settings_type(
        str(root / f"{organization}-{application}.ini"), settings_type.Format.IniFormat)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Abort instead of waiting on any unattended modal prompt.
    def reject_prompt(*args, **kwargs):
        raise RuntimeError("Unexpected GUI prompt during finite pilot")
    for name in ("warning", "critical", "information"):
        setattr(QtWidgets.QMessageBox, name, reject_prompt)
    window = ca.MainWindow()
    window.ui.comboBox_supply.setCurrentIndex(window.ui.comboBox_supply.findData("keithley2636b"))
    window.ui.comboBox_channel.setCurrentIndex(1)
    window.ui.comboBox_keithley_resource.setCurrentText(args.resource)
    window.ui.spinBox_start_current.setValue(1)
    window.ui.spinBox_max_current.setValue(2)
    window.ui.spinBox_max_voltage.setValue(1)
    window.ui.spinBox_step_mA.setValue(0.5)
    window.ui.spinBox_loops.setValue(1)
    window.ui.checkBox_infinite_loops.setChecked(False)
    window.ui.lineEdit_log_dir.setText(str(root))
    window.ui.lineEdit_log_file.setText("triangle-1-to-2mA")
    window.max_voltage_action = "stop"
    commands = []
    readings = []
    original_set = AnnealingKeithley.set_current
    original_measure = AnnealingKeithley.measure
    def checked_set(adapter, current_mA):
        if not 0 <= current_mA <= 2:
            raise RuntimeError("Pilot exceeds 2 mA")
        commands.append((time.monotonic(), current_mA))
        original_set(adapter, current_mA)
    def measured(adapter):
        result = original_measure(adapter)
        readings.append((time.monotonic(), result))
        return result
    AnnealingKeithley.set_current = checked_set
    AnnealingKeithley.measure = measured
    report = dict(resource=args.resource, current_limit_mA=2, compliance_V=1,
                  rates_target_hz=dict(control=50, read=100), output_off_verified=False)
    try:
        window._connect_keithley()
        report["identity"] = window._keithley_adapter.identity
        window.handle_toggle_process_clicked()
        deadline = time.monotonic() + 15
        while window.process_running and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        if window.process_running:
            window.stop_annealing("Pilot timeout", final_state="failed")
        report["state"] = window._process_state
        report["reason"] = window._last_stop_reason
        if window._process_state != "completed":
            raise RuntimeError(f"Pilot did not complete: {window._last_stop_reason}")
    finally:
        if window._keithley_adapter is not None:
            window.stop_annealing("Pilot cleanup")
        report["output_off_verified"] = window._keithley_output_off_verified
        report["commands"] = commands
        report["readings"] = readings
        report["max_programmed_mA"] = max((i for _, i in commands), default=0)
        report["max_measured_mA"] = max((r["current_mA"] for _, r in readings), default=0)
        # Exclude initial configuration / warm-up from steady acquisition spacing.
        for key, entries in (("control", commands[1:]), ("read", readings[1:])):
            gaps = [b[0] - a[0] for a, b in zip(entries, entries[1:])]
            if gaps:
                report[f"{key}_median_hz"] = 1 / statistics.median(gaps)
                report[f"{key}_max_gap_ms"] = max(gaps) * 1000
        output = Path(window.f_name) if window.f_name else None
        if output and output.exists():
            rows = [line for line in output.read_text().splitlines() if line and not line.startswith("#")]
            report["saved_rows"] = len(rows)
            report["data_file"] = str(output)
        (root / "pilot-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        window.close()
        # Independent re-open/read-only check after releasing the controller.
        manager = _default_visa_resource_manager()
        try:
            device = manager.open_resource(args.resource)
            try:
                device.timeout = 1000
                report["independent_output_readback"] = float(device.query("print(smua.source.output)"))
                report["independent_setpoint_A"] = float(device.query("print(smua.source.leveli)"))
            finally:
                device.close()
        finally:
            manager.close()
        (root / "pilot-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        summary = {k: v for k, v in report.items() if k not in ("commands", "readings")}
        print(json.dumps(summary, indent=2))
        if not report["output_off_verified"]:
            raise RuntimeError("Output-OFF was not verified; check hardware immediately.")


if __name__ == "__main__":
    main()
