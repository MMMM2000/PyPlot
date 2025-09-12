# Microwire Data Plotting & Logging

Tools for measuring and visualising data from microwire experiments.  The
repository provides loggers for serial devices and VISA instruments, a generic
data logger and a small launcher that groups all utilities.

## 1. Installation

All commands assume a terminal opened in the repository root.

### 1.1 Windows

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
python -m venv .venv
.\.venv\Scripts\Activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -U pyvisa-py    # optional: update VISA backend
```

### 1.2 macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt
pip install -U pyvisa-py    # optional: update VISA backend
```

### 1.3 Additional tools

The project occasionally uses the Codex CLI for development helpers:

```bash
npm install -g @openai/codex
```

The requirements include `pyvisa`, `pyvisa-py`, `psutil` and `zeroconf` so VISA
resources can be discovered without vendor drivers.

## 2. Master launcher

Run the launcher to access all utilities in a single window:

```bash
python -m launcher
```

Closing the launcher warns about other open windows and will close them.

Plotting scripts opened from the **Plotting** tab keep their settings dialogs
open after generating figures.  Each dialog lists the selected input files and
offers buttons to add or remove entries, so datasets can be refined without
restarting the tool.
Origin sessions are closed automatically after plots are generated so the
Origin application can be closed independently from the Python tools.

## 3. Loggers

### 3.1 Serial Current Annealing Logger

Connects to an HMP4030 power supply via a serial port.  Features include:

* configurable current ramp with optional automatic reversal
* **Reverse current now** button for an immediate ramp down
* default **Reverse to zero after max** behaviour
* automatic halt at **30 V** with a dialog to hold, reverse or stop
* live display of measured current, set current and resistance
* streamlined start-up sequence that begins logging immediately
* plots of resistance vs. current and sample number that follow the system theme
* ignores the initial zero sample when logging and plotting

Launch from the master launcher or run

```bash
python -m data_logging.current_annealing_logger.current_annealing_logger
```

### 3.2 PyVISA Current Annealing Logger

Uses PyVISA to communicate with SCPI instruments over USB, RS‑232 or TCP/IP.  It
mirrors the serial logger’s workflow and adds the same voltage‑limit safety
dialog and live values panel.  Select a VISA resource (e.g.
`ASRL/ttyV1::INSTR`) and start logging.
The ramp-down path is plotted in a contrasting colour to distinguish it from the
current ramp-up, and the first zero sample is ignored just like in the serial
logger.

Launch from the master launcher or run

```bash
python -m data_logging.pyvisa_current_annealing_logger
```

### 3.3 Generic Data Logger

Records arbitrary measurements to structured text files with a built‑in file
name builder.  Real‑time plots update while logging and match the system theme.

## 4. Virtual COM‑port emulator

To test the loggers without hardware start the emulator:

```bash
python -m emulators.virtual_serial_emulator_gui
```

Use **Create Pair (socat)** on Linux/macOS to create `ttyV0` ↔ `ttyV1`.  On
Windows supply a pre‑installed virtual pair or enable the `loop://` option for a
software loopback.  Point the emulator at one side of the pair and the logger at
the other.  All annealing tools default to **115200 baud**.

## 5. Building executables

PyInstaller specifications are provided.  After installing the requirements run

```bash
pyinstaller launcher.spec
```

to create a standalone `dist/launcher` for the current platform.

## 6. Experiments

Prototype user interfaces and plotting experiments live in the `experiments`
directory and are independent from the main tools.

### Data Plotter

`experiments/data_plotter.py` provides a small GUI wrapper around the plotting
modules.  Configure module settings, manage the list of input files, and run the
plotter without the window closing or prompting to save figures afterwards.

## 7. Testing

Run the test suite after installing the requirements:

```bash
pytest -q
```

