# Microwire Data Plotting & Logging

Tools for measuring and visualising data from microwire experiments.  The
repository provides loggers for serial devices and VISA instruments, a generic
data logger and a small launcher that groups all utilities.

## 1. Requirements

* Python 3.10 or newer
* All Python dependencies are listed in `requirements.txt`.
  Install them in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The requirements include `pyvisa`, `pyvisa-py`, `psutil` and `zeroconf` so VISA
resources can be discovered without vendor drivers.

## 2. Virtual COM‑port emulator

To test the loggers without hardware start the emulator:

```bash
python -m emulators.virtual_serial_emulator_gui
```

Use **Create Pair (socat)** on Linux/macOS to create `ttyV0` ↔ `ttyV1`.  On
Windows supply a pre‑installed virtual pair or enable the `loop://` option for a
software loopback.  Point the emulator at one side of the pair and the logger at
the other.  All annealing tools default to **115200 baud**.

## 3. Master launcher

Run `python -m launcher` to open a window that lists all available tools.  The
launcher warns if other windows are still open and will close them when the
launcher exits.

## 4. Loggers

### 4.1 Serial Current Annealing Logger

Connects to an HMP4030 power supply via a serial port.  Features include:

* configurable current ramp with optional automatic reversal
* **Reverse current now** button for an immediate ramp down
* default **Reverse to zero after max** behaviour
* automatic halt when the supply reaches **30 V** – a dialog offers to hold the
  current, reverse to zero or stop the measurement
* live display of measured current, set current and resistance
* plots of resistance vs. current and sample number with backgrounds that follow
  the system light or dark theme

Launch from the master launcher or run

```bash
python -m data_logging.current_annealing_logger.current_annealing_logger
```

### 4.2 PyVISA Current Annealing Logger

Uses PyVISA to communicate with SCPI instruments over USB, RS‑232 or TCP/IP.  It
mirrors the serial logger’s workflow and adds the same voltage‑limit safety
dialog and live values panel.  Select a VISA resource (e.g.
`ASRL/ttyV1::INSTR`) and start logging.

### 4.3 Generic Data Logger

Records arbitrary measurements to structured text files with a built‑in file
name builder.  Real‑time plots update while logging and match the system theme.

## 5. Building executables

PyInstaller specifications are provided.  After installing the requirements run

```bash
pyinstaller launcher.spec
```

to create a standalone `dist/launcher` for the current platform.

## 6. Experiments

Prototype user interfaces and plotting experiments live in the `experiments`
directory and are independent from the main tools.

