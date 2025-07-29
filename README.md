# Python Plot

This repository contains simple tools for logging measurement data and plotting the resulting text files.

## Installation

1. **Install Visual Studio Code**
   - Download from [https://code.visualstudio.com](https://code.visualstudio.com) and follow the installer.
   - After launching VS Code install the **Python** extension when prompted or via the *Extensions* sidebar.

2. **Install Python 3**
   - **Windows**: install from the Microsoft Store or download it from [https://www.python.org](https://www.python.org). Ensure that ``python`` is available in your ``PATH``.
   - **macOS**: download the installer from [https://www.python.org](https://www.python.org) or install via Homebrew.

3. **Open the project**
   - Choose *File → Open Folder* in VS Code and select the cloned repository directory.
   - Open a new terminal inside VS Code with *Terminal → New Terminal*.

4. **Create and activate a virtual environment**
   - ``python -m venv .venv``
   - **Windows**:
     - If activation fails due to execution policy restrictions, run ``Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`` in a PowerShell prompt.
     - Activate with ``.venv\Scripts\activate``.
   - **macOS**: run ``source .venv/bin/activate``.

5. **Install dependencies**
   - ``pip install -r requirements.txt``
   - Alternatively run ``pip install -e .`` to install the project in editable mode.

6. **Start the launcher**
   - ``python launcher.py``

Reactivate the virtual environment whenever you open a new terminal.

## Master launcher

`launcher.py` starts a small GUI that groups all available loggers and
plotting tools. Select an item and press **Run** to launch it:

```bash
python3 launcher.py
```


## Plotting stress dependence data

`pyqt6_plotting/stress_dependence/stress_gui.py` generates plots from a folder of measurement files using a dark themed PyQt6 interface.  The values shown in the GUI come from the **DEFAULT CONFIGURATION** section inside `core.py` and can be adjusted there.

- `DATA_DIR` – directory where your raw `.txt` files live
- `OUTPUT_DIR` – directory in which to save generated plots
- `GLOB_PATTERN` – wildcard pattern for selecting files

Measurement file names must follow:

```
<composition> <title> <sample_end> <anneal> <load><dir>.txt
```

For example `FeSiBP 188_1 s4-2a 68mA 10a.txt`.

The trailing ``a`` or ``b`` in the ``<sample_end>`` field denotes which end of
the microwire was connected.  Throughout this project ``a`` is referred to as
the *marked end* while ``b`` is the *unmarked end*.  Plot titles include these
labels automatically.

Several flags control what variables are plotted and whether the plots are displayed (`SHOW_PLOTS`) or saved (`SAVE_PLOTS`).  Run the plotter with:
```bash
python3 pyqt6_plotting/stress_dependence/stress_gui.py
```

## Comparing Hsw distributions by load

`pyqt6_plotting/hsw_load_compare/load_compare_gui.py` stacks probability density plots for a set of ascending measurement files.  The GUI lets you choose whether to display TT and/or HH curves, show raw data and histograms and keep histogram Y axes shared or independent.

Run the script with:
```bash
python3 pyqt6_plotting/hsw_load_compare/load_compare_gui.py
```

## Plotting Maxion continuous measurements

`pyqt6_plotting/maxion_continuous/maxion_gui.py` visualizes Maxion continuous measurement files and allows plotting raw and/or processed curves for all three channels.  Figures can be displayed and optionally saved.

Run the script with:
```bash
python3 pyqt6_plotting/maxion_continuous/maxion_gui.py
```

## Hsw distribution analysis

`pyqt6_plotting/hsw_distribution/distribution_gui.py` applies a Histogram-Core filter to TT and HH (or T1 and T2) measurements, then plots raw curves, count histograms and probability density curves.  You can choose the column naming scheme in the options dialog.

Launch it with:
```bash
python3 pyqt6_plotting/hsw_distribution/distribution_gui.py
```

## Data logger

`pyqt6_logger/data_logger.py` records serial data to a file.  At the
top of the script you can set the default `LOG_DIR` path as well as the
pre-filled command string and suggested log file name.  `LOG_DIR` can also be
overridden via the `--log-dir` command line option or the `LOG_DIR` environment
variable.  Inside the GUI you can change the directory in which logs are stored
using the **Browse** button next to the *Directory* field.  The interface uses a
dark Fusion palette with rounded buttons for a modern look.

Launch the logger with:

```bash
python3 pyqt6_logger/data_logger.py
```

Use the drop-down boxes to select the serial port and baud rate, then press **Connect to port**.  The **Record** button (or pressing **Enter** in the log-file field) prompts for a file name and stores the log in the directory shown in the *Directory* field.  Log files are always saved with a *.txt* extension.
The port list shows each device's full description to make selection easier.

## Requirements

This project depends on `PyQt6`, `matplotlib`, `numpy` and `pandas`.
Install the dependencies with:

```bash
pip install -r requirements.txt
```

Alternatively the project can be installed in editable mode to make all
modules importable without modifying `sys.path`:

```bash
pip install -e .
```

The `plot-cli` command installed by the package provides a simple wrapper
around the available plotting GUIs. List the supported tools with:

```bash
plot-cli --help
```

Default settings for the stress dependence plotter are stored in
`pyqt6_plotting/default_config.json` and can be customized by providing a
modified path to :func:`pyqt6_plotting.config.load_config`.

## Virtual environment

It is recommended to work inside a Python virtual environment to keep
dependencies isolated.  The steps in the *Installation* section show how
to create and activate a virtual environment on both Windows and macOS.
Once activated, install the requirements with ``pip`` and remember to
activate ``.venv`` again whenever you open a new terminal.

