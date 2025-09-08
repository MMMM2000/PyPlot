#!/usr/bin/env python3
"""
Virtual Serial Emulator – GUI (PyQt6)

What this app does
------------------
• macOS/Linux: one click to create a linked pair of virtual serial ports using `socat`.
  - It makes two predictable paths in the current folder: ./ttyV0  <->  ./ttyV1
  - (Optional) one click to create /dev/cu.ttyV0 and /dev/cu.ttyV1 symlinks (will prompt for sudo).
• All platforms: start/stop a simple data‑logger **emulator** on a selected port.
  - Streams lines like:  T=25.00C I=0.012A V=3.30  at a chosen rate.
  - Responds to *IDN? , RATE? , and RATE <n>.
• Logs everything in the GUI so you can see TX/RX and status.

Windows note
------------
This app cannot *create* virtual COM ports on Windows (that requires a driver like HHD or com0com).
You can still use the **Emulator** panel to talk to any existing COMx or `loop://`.

Dependencies (install once)
---------------------------
python -m pip install pyqt6 pyserial
# macOS/Linux only – for creating the virtual pair
# macOS:  brew install socat
# Linux:  sudo apt-get install socat   (or your distro equivalent)

Run
---
python virtual_serial_emulator_gui.py
"""
from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor
from PyQt6 import QtGui
from PyQt6.QtWidgets import (
    QApplication, QWidget, QGridLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QTextEdit, QGroupBox, QHBoxLayout, QVBoxLayout,
    QCheckBox, QLineEdit, QFileDialog
)

from typing import TYPE_CHECKING, Optional, cast

try:
    import serial  # type: ignore
    from serial.serialutil import SerialBase as _SerialBase  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - environment without pyserial
    serial = None  # type: ignore[assignment]
    class _SerialBase:  # type: ignore[no-redef]
        """Fallback serial base type for type checking."""
        pass

ROOT = Path.cwd()
TTY0 = ROOT / "ttyV0"
TTY1 = ROOT / "ttyV1"


# ------------------------- utility helpers -------------------------

def log_append(widget: QTextEdit, msg: str) -> None:
    widget.append(msg)
    widget.moveCursor(QTextCursor.MoveOperation.End)


def which(cmd: str) -> bool:
    return subprocess.call(["which", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


# ------------------------- emulator threads -------------------------
class BaseEmuThread(threading.Thread):
    def __init__(self, port: str, baud: int, log: QTextEdit):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.log = log
        self._stop = threading.Event()
        self.ser: Optional[_SerialBase] = None

    def stop(self) -> None:
        self._stop.set()

    def _maybe_prefer_cu(self, p: str) -> str:
        """On macOS, prefer /dev/cu.* device when available.

        If ``p`` is a local ./ttyV* symlink or a /dev/tty* path and a matching
        /dev/cu.* exists (created by the GUI button), return the cu path.
        Otherwise return ``p`` unchanged.
        """
        try:
            import platform, os as _os
            if platform.system() != 'Darwin':
                return p
            rp = _os.path.realpath(p)
            # If user created cu.ttyV* symlinks, prefer those by base name
            base = _os.path.basename(p)
            cu_by_base = f"/dev/cu.{base}" if not base.startswith('cu.') else p
            if _os.path.exists(cu_by_base):
                return cu_by_base
            # Otherwise keep the resolved path (often /dev/ttysNNN)
            return rp
        except Exception:
            return p

    def open_serial(self, timeout: float = 0.1) -> bool:
        if serial is None:
            log_append(self.log, "[ERR ] pyserial not installed – run: pip install pyserial")
            return False
        # First attempt (possibly normalized to /dev/cu.* on macOS)
        primary = self._maybe_prefer_cu(self.port)
        try:
            self.ser = serial.serial_for_url(primary, baudrate=self.baud, timeout=timeout)  # type: ignore[union-attr]
            if primary != self.port:
                self.port = primary
            log_append(self.log, f"[INFO] Opened {self.port} @ {self.baud} baud")
            return True
        except Exception as e:
            log_append(self.log, f"[WARN] Open failed on {primary}: {e}")
        # Fallback: lower baudrates commonly supported by PTYs
        for b in (115200, 9600):
            try:
                self.ser = serial.serial_for_url(primary, baudrate=b, timeout=timeout)  # type: ignore[union-attr]
                self.baud = b
                if primary != self.port:
                    self.port = primary
                log_append(self.log, f"[INFO] Opened {self.port} with fallback baud {b}")
                return True
            except Exception as e:
                log_append(self.log, f"[WARN] Fallback {b} failed: {e}")
        return False

    def close_serial(self) -> None:
        try:
            if self.ser is not None:
                self.ser.close()
                log_append(self.log, "[INFO] Serial closed")
        except Exception:
            pass


# ---- Serial Data Logger mode (semicolon numeric lines; MODE/RATE support)
from pathlib import Path as _Path
import re as _re

ROOT_DIR = _Path(__file__).resolve().parents[1]


def _stress_path(load_g: float, direction: str) -> _Path:
    if abs(load_g - round(load_g)) < 1e-9:
        load_s = f"{int(round(load_g))}"
    else:
        load_s = str(load_g).replace(".", ",")
    tail = f"{load_s}{direction.lower()}"
    base = ROOT_DIR / "sample_data" / "stress_dependence" / "FeSiB 85_10 s2-2a 47mA"
    return base / f"FeSiB 85_10 s2-2a 47mA {tail}.txt"


def _temp_path(temp_sel: str) -> _Path:
    base = ROOT_DIR / "sample_data" / "temperature_dependence"
    temp_sel = temp_sel.strip()
    if temp_sel == "25-100C":
        suffix = "overall"
    else:
        suffix = temp_sel
    return base / f"Fe77Mo4B18Cu1 4_3 77mA {suffix}.txt"


def _maxion_path() -> _Path:
    return ROOT_DIR / "sample_data" / "Maxion" / "1 final 2 coils.txt"


def _read_lines(path: _Path) -> list[str]:
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return [ln.strip() for ln in f if ln.strip()]


class DataLoggerEmuThread(BaseEmuThread):
    def __init__(self, port: str, baud: int, rate_hz: int, log: QTextEdit):
        super().__init__(port, baud, log)
        self.rate_hz = max(1, int(rate_hz))
        self.lines: list[str] = _read_lines(_stress_path(2.5, "a"))
        self.idx = 0
        self.streaming = True

    def run(self) -> None:
        if not self.open_serial(timeout=0):
            return
        delay = 1.0 / max(self.rate_hz, 1)
        next_send = time.perf_counter()
        while not self._stop.is_set():
            try:
                raw = self.ser.readline() if self.ser else b""
            except Exception:
                raw = b""
            if raw:
                cmd = raw.decode(errors="ignore").strip()
                log_append(self.log, f"[RX  ] {cmd}")
                u = cmd.upper()
                if u == "*IDN?":
                    try:
                        if self.ser is not None:
                            self.ser.write(b"GEN,DataEmu,0,0\n")
                    except Exception:
                        pass
                elif u == "RATE?":
                    try:
                        if self.ser is not None:
                            self.ser.write(f"{self.rate_hz}\n".encode())
                    except Exception:
                        pass
                elif u.startswith("RATE"):
                    m = _re.search(r"(\d+)", u)
                    if m:
                        self.rate_hz = max(1, int(m.group(1)))
                        delay = 1.0 / self.rate_hz
                        next_send = time.perf_counter() + delay
                        try:
                            if self.ser is not None:
                                self.ser.write(f"{self.rate_hz}\n".encode())
                        except Exception:
                            pass
                else:
                    m = _re.match(r"^MODE\s+(\w+)(.*)$", cmd, _re.IGNORECASE)
                    if m:
                        mode = m.group(1).upper()
                        rest = m.group(2)
                        args: dict[str, str] = {}
                        for token in _re.split(r"\s+|;", rest):
                            if token and "=" in token:
                                k, v = token.split("=", 1)
                                args[k.strip().upper()] = v.strip()
                        try:
                            if mode == "STRESS":
                                load = float(args.get("LOAD", "2.5"))
                                d = args.get("DIR", "a")
                                p = _stress_path(load, d)
                            elif mode in ("TEMP", "TEMPERATURE"):
                                t = args.get("T", "25C")
                                p = _temp_path(t)
                            elif mode == "MAXION":
                                p = _maxion_path()
                            else:
                                p = None
                            if p is not None:
                                self.lines = _read_lines(p)
                                self.idx = 0
                                log_append(self.log, f"[INFO] MODE {mode}: {p.name}")
                        except Exception as e:
                            log_append(self.log, f"[WARN] MODE change failed: {e}")

            now = time.perf_counter()
            if self.streaming and self.lines and now >= next_send:
                line = self.lines[self.idx % len(self.lines)] + "\n"
                self.idx += 1
                try:
                    if self.ser:
                        self.ser.write(line.encode())
                except Exception as e:
                    log_append(self.log, f"[ERR ] write failed: {e}")
                next_send += delay
            sleep_for = max(0.0005, next_send - time.perf_counter())
            time.sleep(min(0.001, sleep_for))
        self.close_serial()


# ---- Current Annealing Logger mode (HMP4030 subset)
def _ca_default_sample() -> _Path:
    return ROOT_DIR / "sample_data" / "current_annealing" / "Ni51Fe26Ga21 1_2 s2 1000mA.txt"


def _load_ca_samples(path: _Path) -> list[tuple[float, float, float]]:
    samples: list[tuple[float, float, float]] = []
    try:
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                cur, volt, res = map(float, parts[:3])
                samples.append((cur, volt, res))
    except Exception as e:
        samples = []
    return samples


class AnnealingEmuThread(BaseEmuThread):
    def __init__(self, port: str, baud: int, sample_path: _Path, log: QTextEdit):
        super().__init__(port, baud, log)
        self.samples = _load_ca_samples(sample_path)
        self.idx = 0

    def run(self) -> None:
        if not self.open_serial(timeout=0.1):
            return
        while not self._stop.is_set():
            try:
                raw = self.ser.readline() if self.ser else b""
            except Exception:
                raw = b""
            if not raw:
                time.sleep(0.001)
                continue
            cmd = raw.decode(errors="ignore").strip()
            log_append(self.log, f"[RX  ] {cmd}")
            u = cmd.upper()
            try:
                if self.ser is None:
                    continue
                if u == "*IDN?":
                    self.ser.write(b"HMP4030,Emulator,0,0\n")
                elif u.startswith("MEAS:VOLT"):
                    v = self.samples[self.idx][1] if self.idx < len(self.samples) else 0.0
                    self.ser.write(f"{v}\n".encode())
                elif u.startswith("MEAS:CURR"):
                    if self.idx < len(self.samples):
                        i = self.samples[self.idx][0]
                        self.idx += 1
                    else:
                        i = 0.0
                    self.ser.write(f"{i}\n".encode())
                else:
                    # Ignore other SCPI config commands
                    pass
            except Exception as e:
                log_append(self.log, f"[ERR ] write failed: {e}")
        self.close_serial()


# ------------------------- main window -------------------------
class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Virtual Serial Emulator – GUI")
        self.resize(880, 600)
        self.socat_proc: Optional[subprocess.Popen] = None
        self.emu: Optional[threading.Thread] = None
        self._build_ui()

    # UI layout
    def _build_ui(self):
        layout = QGridLayout(self)

        # Pair group
        pair_box = QGroupBox("Port Helpers (macOS/Linux)")
        pb_layout = QGridLayout(pair_box)
        self.lbl_tty0 = QLabel("./ttyV0")
        self.lbl_tty1 = QLabel("./ttyV1")
        self.btn_pair = QPushButton("Create Pair (socat)")
        self.btn_pair.hide()
        self.btn_symlinks = QPushButton("Create /dev/cu.ttyV* symlinks (sudo)")
        self.btn_symlinks.clicked.connect(self.on_symlinks)
        self.btn_kill = QPushButton("Stop Pair")
        self.btn_kill.hide()
        pb_layout.addWidget(self.btn_symlinks, 0, 0)
        pb_layout.addWidget(self.lbl_tty0,   1, 0, 1, 3)
        pb_layout.addWidget(self.lbl_tty1,   2, 0, 1, 3)

        # Emulator group
        emu_box = QGroupBox("Emulator")
        eb = QGridLayout(emu_box)
        self.cmb_port = QComboBox()
        self.btn_refresh = QPushButton("Refresh Ports")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.chk_auto_refresh = QCheckBox("Auto refresh ports")
        self.chk_auto_refresh.stateChanged.connect(self.on_auto_refresh_changed)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2000)
        self.refresh_timer.timeout.connect(self.refresh_ports)
        # Mode selection
        self.cmb_mode = QComboBox(); self.cmb_mode.addItems(["Serial Data Logger", "Current Annealing Logger"])
        self.cmb_mode.currentIndexChanged.connect(self.on_mode_changed)
        # Presets are applied automatically on mode change; remove button
        # to simplify UI.
        # self.btn_preset = QPushButton("Apply Preset")
        # self.btn_preset.clicked.connect(self.on_apply_preset)
        # Baud selection like in data loggers
        self.combo_baud = QComboBox(); self.combo_baud.addItems([
            "921600", "460800", "115200", "57600", "19200", "9600"
        ])
        self.combo_baud.setCurrentText("921600")
        # Data rate only for Serial Data Logger
        self.spin_rate = QSpinBox(); self.spin_rate.setRange(1, 2000); self.spin_rate.setValue(1000)
        # Sample file only for Current Annealing
        self.edit_sample = QLineEdit(str(_ca_default_sample()))
        self.btn_sample = QPushButton("Browse…")
        self.btn_sample.clicked.connect(self.on_browse_sample)
        self.chk_loop = QCheckBox("Use loop:// (no external port)")
        self.chk_loop.setToolTip("Enable a software loopback port. Useful when you don't have a real/virtual COM pair. The logger should also select 'loop://' to connect.")
        self.chk_loop.stateChanged.connect(self.on_loop_changed)
        self.btn_start = QPushButton("Start Emulator")
        self.btn_stop = QPushButton("Stop Emulator"); self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop.clicked.connect(self.on_stop)
        row = 0
        eb.addWidget(QLabel("Mode:"), row, 0)
        eb.addWidget(self.cmb_mode, row, 1); row += 1
        eb.addWidget(QLabel("Port:"), row, 0)
        eb.addWidget(self.cmb_port,   row, 1)
        eb.addWidget(self.btn_refresh,row, 2); row += 1
        eb.addWidget(self.chk_auto_refresh, row, 1); row += 1
        eb.addWidget(QLabel("Baud:"),row, 0)
        eb.addWidget(self.combo_baud, row, 1); row += 1
        self.lbl_rate = QLabel("Rate Hz:")
        eb.addWidget(self.lbl_rate, row, 0)
        eb.addWidget(self.spin_rate, row, 1); row += 1
        self.lbl_sample = QLabel("Sample file:")
        eb.addWidget(self.lbl_sample, row, 0)
        eb.addWidget(self.edit_sample, row, 1)
        eb.addWidget(self.btn_sample, row, 2); row += 1
        eb.addWidget(self.chk_loop,  row, 0, 1, 3); row += 1
        # (Presets auto-apply on mode change; no extra button here)
        eb.addWidget(self.btn_start, row, 0)
        eb.addWidget(self.btn_stop,  row, 1)

        # Log
        self.log = QTextEdit(); self.log.setReadOnly(True)

        layout.addWidget(pair_box, 0, 0)
        layout.addWidget(emu_box,  1, 0)
        layout.addWidget(self.log, 2, 0)

        # initial
        self.refresh_ports()
        self.on_mode_changed()

    # -------- pair handling ---------
    def on_pair(self):
        if platform.system() not in {"Darwin", "Linux"}:
            log_append(self.log, "[WARN] Pair creation is only available on macOS/Linux.")
            return
        if not which("socat"):
            log_append(self.log, "[ERR ] 'socat' not found. macOS: brew install socat; Linux: install via your package manager.")
            return
        # Clean old links
        for p in (TTY0, TTY1):
            try:
                if p.exists() or p.is_symlink():
                    p.unlink()
            except Exception:
                pass
        cmd = ["socat", "-d", "-d", "-v", f"pty,rawer,echo=0,link={TTY0}", f"pty,rawer,echo=0,link={TTY1}"]
        try:
            self.socat_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.btn_kill.setEnabled(True)
            log_append(self.log, "[INFO] Starting socat… creating ./ttyV0 <-> ./ttyV1")
        except Exception as e:
            log_append(self.log, f"[ERR ] Failed to start socat: {e}")
            return
        # poll until links exist
        t0 = time.time()
        while time.time() - t0 < 5:
            if TTY0.exists() and TTY1.exists():
                break
            QApplication.processEvents(); time.sleep(0.05)
        self.lbl_tty0.setText(str(TTY0 if TTY0.exists() else "./ttyV0 – not found yet"))
        self.lbl_tty1.setText(str(TTY1 if TTY1.exists() else "./ttyV1 – not found yet"))
        if TTY0.exists():
            log_append(self.log, f"[INFO] Created: {TTY0}")
        if TTY1.exists():
            log_append(self.log, f"[INFO] Created: {TTY1}")
        self.refresh_ports()

    def on_symlinks(self):
        if platform.system() not in {"Darwin", "Linux"}:
            log_append(self.log, "[WARN] Symlinks are only relevant on macOS/Linux.")
            return
        # Run in a background thread to avoid freezing the GUI in case of auth prompt
        def worker():
            for idx, src in enumerate((TTY0, TTY1)):
                dst = Path(f"/dev/cu.ttyV{idx}")
                if not src.exists():
                    log_append(self.log, f"[WARN] {src} does not exist yet – create the pair first.")
                    continue
                if dst.exists():
                    log_append(self.log, f"[INFO] Symlink already exists: {dst}")
                    continue
                try:
                    if platform.system() == "Darwin":
                        # Use AppleScript to prompt for admin privileges without blocking the main thread
                        cmd = f'do shell script "ln -s {src} {dst}" with administrator privileges'
                        r = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True)
                        if r.returncode == 0:
                            log_append(self.log, f"[INFO] Created {dst} → {src}")
                        else:
                            log_append(self.log, f"[WARN] Could not create {dst}: {r.stderr.strip()}")
                    else:
                        # Linux: prompt in terminal-less context is tricky; attempt direct ln, likely no sudo needed
                        subprocess.run(["ln", "-s", str(src), str(dst)], check=False)
                        if dst.exists():
                            log_append(self.log, f"[INFO] Created {dst} → {src}")
                        else:
                            log_append(self.log, f"[WARN] Could not create {dst}. You can still select {src} directly.")
                except Exception as e:
                    log_append(self.log, f"[WARN] Symlink creation failed: {e}")
            # Refresh list back on the GUI thread
            self.refresh_ports()

        threading.Thread(target=worker, daemon=True).start()

    def on_kill_pair(self):
        if self.socat_proc is not None:
            try:
                self.socat_proc.terminate()
                log_append(self.log, "[INFO] Stopped socat pair")
            except Exception:
                pass
            self.socat_proc = None
            self.btn_kill.setEnabled(False)

    # -------- emulator handling ---------
    def on_loop_changed(self, state):
        self.cmb_port.setEnabled(not self.chk_loop.isChecked())

    def on_start(self):
        # Prefer stored device path (userData). Fallback to visible text.
        if self.chk_loop.isChecked():
            port = "loop://"
        else:
            data = self.cmb_port.currentData()
            port = str(data) if data else self.cmb_port.currentText().split(" - ")[0].strip()
        if not port:
            log_append(self.log, "[ERR ] No port selected")
            return
        try:
            baud = int(self.combo_baud.currentText())
        except Exception:
            baud = 9600
        if serial is not None:
            try:
                test = serial.serial_for_url(port, baudrate=baud, timeout=0)
                test.close()
            except Exception as e:
                log_append(self.log, f"[ERR ] Cannot open {port}: {e}")
                return
        if self.cmb_mode.currentText().startswith("Serial"):
            rate = int(self.spin_rate.value())
            self.emu = DataLoggerEmuThread(port, baud, rate, self.log)
        else:
            path = _Path(self.edit_sample.text().strip())
            if not path.exists():
                log_append(self.log, f"[WARN] Sample file not found: {path}")
            self.emu = AnnealingEmuThread(port, baud, path, self.log)
        self.emu.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

    def on_stop(self):
        if self.emu and hasattr(self.emu, 'stop'):
            try:
                self.emu.stop()  # type: ignore[attr-defined]
            except Exception:
                pass
        if self.emu:
            try:
                self.emu.join(timeout=2)
            except Exception:
                pass
        self.emu = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    # -------- ports listing ---------
    def refresh_ports(self):
        """Populate the port list with only actually available ports.

        - Windows: use pyserial list_ports for COMx with friendly descriptions.
        - macOS/Linux: include common /dev/cu.* and /dev/tty.* plus local ./ttyV*.
        """
        self.cmb_port.clear()
        sysname = platform.system()

        # Always offer local virtual pair paths if present
        local_syms: list[tuple[str, str]] = []
        if TTY0.exists():
            local_syms.append((str(TTY0), str(TTY0)))
        if TTY1.exists():
            local_syms.append((str(TTY1), str(TTY1)))

        if sysname == "Windows":
            # Use pyserial enumeration to show only real COM ports with names
            try:
                from serial.tools import list_ports  # type: ignore
                ports = list(list_ports.comports())
            except Exception:
                ports = []
            for p in ports:
                try:
                    dev = getattr(p, 'device', '') or getattr(p, 'name', '')
                    desc = getattr(p, 'description', '') or 'Serial Port'
                    if dev:
                        label = f"{dev} - {desc}" if desc else dev
                        self.cmb_port.addItem(label, userData=dev)
                except Exception:
                    continue
        else:
            # macOS/Linux: prefer real devices in /dev; still show local symlinks
            from glob import glob
            paths = sorted(set(glob("/dev/cu.*") + glob("/dev/tty.*")))
            for dev in paths:
                self.cmb_port.addItem(dev, userData=dev)

        # Prepend local virtual pair entries (if any) for quick access
        for label, dev in reversed(local_syms):
            self.cmb_port.insertItem(0, label, userData=dev)

        self._update_pair_buttons_enabled()

    def on_auto_refresh_changed(self, state):
        if self.chk_auto_refresh.isChecked():
            self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

    def on_apply_preset(self) -> None:
        is_serial = self.cmb_mode.currentText().startswith("Serial")
        # Set recommended baud and mode-specific fields
        self.combo_baud.setCurrentText("921600" if is_serial else "9600")
        if is_serial:
            self.spin_rate.setValue(1000)
        else:
            self.edit_sample.setText(str(_ca_default_sample()))
        # Select first available port, else enable loop://
        if self.cmb_port.count() > 0:
            self.cmb_port.setCurrentIndex(0)
            self.chk_loop.setChecked(False)
        else:
            self.chk_loop.setChecked(True)

    def _update_pair_buttons_enabled(self) -> None:
        # Buttons were removed; keep method for backward calls
        if hasattr(self, 'btn_preset_pair0') and hasattr(self, 'btn_preset_pair1'):
            sysname = platform.system()
            ok_platform = sysname in {"Darwin", "Linux"}
            have0 = TTY0.exists() or Path("/dev/cu.ttyV0").exists()
            have1 = TTY1.exists() or Path("/dev/cu.ttyV1").exists()
            enabled0 = ok_platform and have0
            enabled1 = ok_platform and have1
            try:
                self.btn_preset_pair0.setEnabled(enabled0)
                self.btn_preset_pair1.setEnabled(enabled1)
            except Exception:
                pass

    def on_use_pair_port(self, emu_port: str, logger_port: str) -> None:
        if not Path(emu_port).exists():
            log_append(self.log, f"[WARN] {emu_port} not found. Create the pair first.")
            return
        idx = self.cmb_port.findText(emu_port)
        if idx >= 0:
            self.cmb_port.setCurrentIndex(idx)
        else:
            self.cmb_port.insertItem(0, emu_port, userData=emu_port)
            self.cmb_port.setCurrentIndex(0)
        self.chk_loop.setChecked(False)
        log_append(self.log, f"[INFO] Emulator will use {emu_port}. Point your logger to {logger_port}.")

    def _best_pair_path(self, index: int) -> str:
        # Prefer /dev/cu.ttyV* symlink if present; else local ./ttyV*
        p = Path(f"/dev/cu.ttyV{index}")
        if p.exists():
            return str(p)
        return str((TTY0 if index == 0 else TTY1))

    def on_mode_changed(self) -> None:
        is_serial = self.cmb_mode.currentText().startswith("Serial")
        # Recommended baud per mode
        self.combo_baud.setCurrentText("921600" if is_serial else "9600")
        # Show/Hide controls per mode
        self.lbl_rate.setVisible(is_serial)
        self.spin_rate.setVisible(is_serial)
        self.lbl_sample.setVisible(not is_serial)
        self.edit_sample.setVisible(not is_serial)
        self.btn_sample.setVisible(not is_serial)

    def on_browse_sample(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select sample file", str(_ca_default_sample()))
        if path:
            self.edit_sample.setText(path)

    # -------- lifecycle ---------
    def closeEvent(self, a0: 'QtGui.QCloseEvent'):  # type: ignore[override]
        self.on_stop()
        self.on_kill_pair()
        return super().closeEvent(a0)


from plotting.utils import apply_system_theme


def main() -> QWidget | None:
    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv)
        owns_app = True
    # Match the app theme with the system
    try:
        from PyQt6 import QtWidgets as _QtW
        apply_system_theme(cast(_QtW.QApplication, app))
    except Exception:
        pass
    w = Main()
    w.show()
    # Handle Ctrl+C properly
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    except Exception:
        pass
    if owns_app:
        sys.exit(app.exec())
        return None
    else:
        return w


if __name__ == "__main__":
    main()
