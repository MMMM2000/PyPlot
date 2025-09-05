"""Replay current annealing data over a virtual serial port.

The emulator loads measurements from a text file with three columns:
current (A), voltage (V) and resistance (Ohm). It answers a subset of
SCPI commands used by :mod:`data_logging.current_annealing_logger`. After
the last sample a zero current response is sent to simulate the
microwire burning through.
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path
from typing import Iterable, Tuple, List, Tuple as TT

import serial
from PyQt6 import QtWidgets
from PyQt6.QtSerialPort import QSerialPortInfo

SAMPLE_FILE = (
    Path(__file__).resolve().parents[1]
    / "sample_data"
    / "current_annealing"
    / "Ni51Fe26Ga21 1_2 s2 1000mA.txt"
)


def _enumerate_ports() -> List[TT[str, str]]:
    """Return list of (label, port_name) for available serial ports.

    Label format mirrors the data logger: "COMx - Description" if available.
    """
    ports: List[TT[str, str]] = []
    for info in QSerialPortInfo.availablePorts():
        label = info.portName()
        if info.description():
            label += f" - {info.description()}"
        ports.append((label, info.portName()))
    return ports


def _select_port_dialog(default_port: str | None = None) -> str | None:
    """Show a modal dialog with a combo box and Refresh button.

    Returns selected port name, or None if cancelled.
    """
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("Virtual COM Port")
    layout = QtWidgets.QVBoxLayout(dlg)

    row = QtWidgets.QHBoxLayout()
    row.addWidget(QtWidgets.QLabel("Port:"))
    combo = QtWidgets.QComboBox()
    refresh_btn = QtWidgets.QPushButton("Refresh")
    row.addWidget(combo)
    row.addWidget(refresh_btn)
    layout.addLayout(row)

    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok
        | QtWidgets.QDialogButtonBox.StandardButton.Cancel
    )
    layout.addWidget(buttons)

    def populate() -> None:
        combo.clear()
        items = _enumerate_ports()
        for label, name in items:
            combo.addItem(label, userData=name)
        # preselect default if available
        if default_port:
            for i in range(combo.count()):
                if str(combo.itemData(i)).upper() == str(default_port).upper():
                    combo.setCurrentIndex(i)
                    break

    populate()
    refresh_btn.clicked.connect(populate)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
        if combo.count() == 0:
            return None
        return str(combo.currentData())
    return None

def load_samples(path: Path) -> list[Tuple[float, float, float]]:
    """Load tab or space separated samples from *path*."""
    samples: list[Tuple[float, float, float]] = []
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            cur, volt, res = map(float, parts[:3])
            samples.append((cur, volt, res))
    return samples

def run(port: str, baudrate: int, data: Iterable[Tuple[float, float, float]]) -> None:
    """Run the blocking emulator loop on *port* using *data*."""
    samples = list(data)
    index = 0
    with serial.Serial(port, baudrate, timeout=1) as ser:
        while True:
            cmd = ser.readline().decode(errors="ignore").strip()
            if not cmd:
                continue
            cmd_u = cmd.upper()
            if cmd_u == "*IDN?":
                ser.write(b"HMP4030,Emulator,0,0\n")
            elif cmd_u.startswith("MEAS:VOLT"):
                if index < len(samples):
                    ser.write(f"{samples[index][1]}\n".encode())
                else:
                    ser.write(b"0\n")
            elif cmd_u.startswith("MEAS:CURR"):
                if index < len(samples):
                    ser.write(f"{samples[index][0]}\n".encode())
                    index += 1
                else:
                    ser.write(b"0\n")
                    break
            else:
                # Most configuration commands do not respond
                ser.write(b"OK\n")

def thread_main(port: str, baudrate: int, sample_file: Path) -> None:
    data = load_samples(sample_file)
    run(port, baudrate, data)

def main() -> None:
    parser = argparse.ArgumentParser(description="Current annealing serial emulator")
    parser.add_argument("--port", default="COM6", help="Serial port to serve")
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--sample", type=Path, default=SAMPLE_FILE)
    args = parser.parse_args([] if QtWidgets.QApplication.instance() else None)

    if QtWidgets.QApplication.instance():
        # Prompt for port when launched from the GUI using a dialog with Refresh
        selected = _select_port_dialog(default_port=args.port)
        if not selected:
            # If user cancelled or no ports, fall back to manual entry
            port, ok = QtWidgets.QInputDialog.getText(
                None, "Virtual COM port", "Port:", text=args.port
            )
            if not ok or not port:
                return None
            args.port = port
        else:
            args.port = selected
        threading.Thread(
            target=thread_main,
            args=(args.port, args.baudrate, args.sample),
            daemon=True,
        ).start()
        QtWidgets.QMessageBox.information(
            None,
            "Emulator running",
            f"Serving {args.port}. Close this dialog to continue.",
        )
    else:
        thread_main(args.port, args.baudrate, args.sample)

    return None

if __name__ == "__main__":
    main()
