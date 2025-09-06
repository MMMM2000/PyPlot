"""Emulator for the generic Serial Data Logger.

Streams semicolon-separated numeric data over a virtual COM port.

- Default rate: 1000 Hz (configurable via GUI or RATE command)
- Dataset depends on MODE commands sent by the logger:
  - "MODE STRESS LOAD=<g> DIR=<a|b>" -> pick stress dependence file
  - "MODE TEMP T=<25C|25-100C|100C>" -> pick temperature dependence file
  - "MODE MAXION" -> pick Maxion file (three channels)

The emulator also answers a few queries:
  - "*IDN?" -> identification string
  - "RATE?" -> current rate in Hz
  - "RATE <hz>" or "RATE=<hz>" -> set rate

Data is streamed continuously; when the end of a file is reached, it
wraps and continues from the beginning.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Iterable, List

import serial
from PyQt6 import QtWidgets
from PyQt6.QtSerialPort import QSerialPortInfo

ROOT = Path(__file__).resolve().parents[1]


def _stress_path(load_g: float, direction: str) -> Path:
    # Loads are formatted with comma as decimal separator in filenames
    # e.g., "2,5a.txt", "12,5b.txt", "10a.txt"
    if abs(load_g - round(load_g)) < 1e-9:
        load_s = f"{int(round(load_g))}"
    else:
        # Replace dot with comma
        load_s = str(load_g).replace(".", ",")
    tail = f"{load_s}{direction.lower()}"
    base = ROOT / "sample_data" / "stress_dependence" / "FeSiB 85_10 s2-2a 47mA"
    return base / f"FeSiB 85_10 s2-2a 47mA {tail}.txt"


def _temp_path(temp_sel: str) -> Path:
    base = ROOT / "sample_data" / "temperature_dependence"
    temp_sel = temp_sel.strip()
    if temp_sel == "25-100C":
        suffix = "overall"
    else:
        suffix = temp_sel
    return base / f"Fe77Mo4B18Cu1 4_3 77mA {suffix}.txt"


def _maxion_path() -> Path:
    return ROOT / "sample_data" / "Maxion" / "1 final 2 coils.txt"


def _read_lines(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return [ln.strip() for ln in f if ln.strip()]


def _enumerate_ports() -> list[tuple[str, str]]:
    ports: list[tuple[str, str]] = []
    for info in QSerialPortInfo.availablePorts():
        label = info.portName()
        if info.description():
            label += f" - {info.description()}"
        ports.append((label, info.portName()))
    return ports


def _select_port_and_rate(default_port: str, default_rate: int) -> tuple[str | None, int | None]:
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("Data Logger Emulator")
    layout = QtWidgets.QGridLayout(dlg)

    layout.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
    combo = QtWidgets.QComboBox()
    refresh = QtWidgets.QPushButton("Refresh")
    layout.addWidget(combo, 0, 1)
    layout.addWidget(refresh, 0, 2)

    layout.addWidget(QtWidgets.QLabel("Rate (Hz):"), 1, 0)
    rate = QtWidgets.QSpinBox()
    rate.setRange(1, 1_000_000)
    rate.setValue(default_rate)
    layout.addWidget(rate, 1, 1)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
    )
    layout.addWidget(buttons, 2, 0, 1, 3)

    def populate() -> None:
        combo.clear()
        for label, name in _enumerate_ports():
            combo.addItem(label, userData=name)
        for i in range(combo.count()):
            if str(combo.itemData(i)).upper() == default_port.upper():
                combo.setCurrentIndex(i)
                break

    refresh.clicked.connect(populate)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    populate()

    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None, None
    if combo.count() == 0:
        return None, None
    return str(combo.currentData()), int(rate.value())


class StreamState:
    def __init__(self) -> None:
        self.mode = "STRESS"
        self.params: dict[str, str] = {"LOAD": "2.5", "DIR": "a"}
        self.rate_hz = 1000
        self.lines: List[str] = _read_lines(_stress_path(2.5, "a"))
        self.idx = 0
        self.streaming = True

    def set_mode(self, mode: str, args: dict[str, str]) -> None:
        self.mode = mode
        self.params.update(args)
        try:
            if mode == "STRESS":
                load = float(self.params.get("LOAD", "2.5"))
                direction = self.params.get("DIR", "a")
                path = _stress_path(load, direction)
            elif mode in ("TEMP", "TEMPERATURE"):
                t = self.params.get("T", "25C")
                path = _temp_path(t)
            elif mode == "MAXION":
                path = _maxion_path()
            else:
                return
            self.lines = _read_lines(path)
            self.idx = 0
        except FileNotFoundError:
            # Keep previous dataset if requested one is missing
            pass


def _parse_mode_cmd(s: str) -> tuple[str, dict[str, str]] | None:
    # Accept: MODE XYZ [KEY=VAL]...
    m = re.match(r"^MODE\s+(\w+)(.*)$", s, re.IGNORECASE)
    if not m:
        return None
    mode = m.group(1).upper()
    rest = m.group(2).strip()
    args: dict[str, str] = {}
    for token in re.split(r"\s+|;", rest):
        if not token:
            continue
        if "=" in token:
            k, v = token.split("=", 1)
            args[k.strip().upper()] = v.strip()
    return mode, args


def run(port: str, baud: int, init_rate: int) -> None:
    state = StreamState()
    state.rate_hz = max(1, int(init_rate))
    delay = 1.0 / state.rate_hz
    last_send = time.perf_counter()
    with serial.Serial(port, baud, timeout=0) as ser:
        while True:
            # Read incoming commands (non-blocking)
            try:
                raw = ser.readline()
            except Exception:
                raw = b""
            if raw:
                cmd = raw.decode(errors="ignore").strip()
                u = cmd.upper()
                if u == "*IDN?":
                    ser.write(b"GEN,DataEmu,0,0\n")
                elif u.startswith("RATE"):
                    m = re.search(r"(\d+)", u)
                    if m:
                        state.rate_hz = max(1, int(m.group(1)))
                        delay = 1.0 / state.rate_hz
                    else:
                        ser.write(f"{state.rate_hz}\n".encode())
                elif u in ("START", "RUN"):
                    state.streaming = True
                elif u in ("STOP", "PAUSE"):
                    state.streaming = False
                else:
                    parsed = _parse_mode_cmd(cmd)
                    if parsed is not None:
                        mode, args = parsed
                        state.set_mode(mode, args)

            now = time.perf_counter()
            if state.streaming and (now - last_send) >= delay and state.lines:
                line = state.lines[state.idx % len(state.lines)]
                state.idx += 1
                ser.write((line + "\n").encode())
                last_send = now
            # Avoid busy loop
            time.sleep(0.0005)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic Serial Data Logger emulator")
    parser.add_argument("--port", default="COM7", help="Serial port to serve")
    parser.add_argument("--baudrate", type=int, default=921600)
    parser.add_argument("--rate", type=int, default=1000, help="Streaming rate (Hz)")
    args = parser.parse_args([] if QtWidgets.QApplication.instance() else None)

    if QtWidgets.QApplication.instance():
        sel_port, sel_rate = _select_port_and_rate(args.port, args.rate)
        if not sel_port or sel_rate is None:
            return None
        args.port = sel_port
        args.rate = int(sel_rate)
        QtWidgets.QMessageBox.information(
            None,
            "Emulator running",
            f"Serving {args.port} at {args.rate} Hz. Close this dialog to continue.",
        )
        # Run in the foreground; caller's GUI continues
        import threading

        threading.Thread(target=run, args=(args.port, args.baudrate, args.rate), daemon=True).start()
        return None
    else:
        run(args.port, args.baudrate, args.rate)


if __name__ == "__main__":
    main()

