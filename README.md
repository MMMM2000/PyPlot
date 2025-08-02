# Python Plot

This repository contains simple tools for logging measurement data and plotting the resulting text files.

## Installation

The steps below assume no prior Python knowledge. **Commands** shown in fixed-width
font should be typed into a terminal exactly as written and confirmed with the
``Enter`` key.

1. **Install Visual Studio Code**
   - Download from [https://code.visualstudio.com](https://code.visualstudio.com) and run the installer.
   - After launching VS Code install the **Python** extension when prompted or
     open the *Extensions* sidebar and search for "Python".

2. **Install Python 3**
   - Visit [https://www.python.org](https://www.python.org) and download the latest
     Python **3** release.
   - **Windows**: during installation enable the *Add python.exe to PATH* option so
     ``python`` and ``pip`` work from a terminal.
   - **macOS**: either run the installer downloaded above or install with Homebrew.
   - Check the installation by running ``python --version`` in a terminal.

3. **Open the project**
   - Choose *File → Open Folder* in VS Code and select the repository directory
     that contains this README.
   - Open a new terminal inside VS Code with *Terminal → New Terminal*.

4. **Create and activate a virtual environment**
   - Run ``python -m venv .venv`` to create a folder named ``.venv``.
   - **Windows**:
     - Activation may fail with an "execution policy" error. In that case run
       ``Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`` in a
       separate PowerShell window and try again.
     - Activate with ``.venv\Scripts\activate``.
   - **macOS** or Linux: run ``source .venv/bin/activate``.
   - After activation the terminal prompt usually shows ``(.venv)`` at the start.

5. **Install dependencies**
   - First upgrade ``pip`` (Python's package installer) with ``python -m pip install --upgrade pip``.
   - Install the required packages with ``pip install -r requirements.txt``. Make
     sure you are in the repository folder so ``requirements.txt`` is found.
   - Alternatively run ``pip install -e .`` to install the project in editable mode.

6. **Start the launcher**
   - Run ``python launcher.py`` to open the graphical tool window.

Reactivate the virtual environment whenever you open a new terminal. Repeat the
``activate`` command from step 4 before running ``python`` or ``pip`` again.

## Master launcher

`launcher.py` starts a small GUI that groups all available loggers and
plotting tools (Stress Dependence, Temperature Sensitivity and others).
Select an item and press **Run** to launch it:

```bash
python3 launcher.py
```


## Plotting stress dependence data

`plotting/stress_dependence/stress_gui.py` generates plots from a folder of measurement files using a dark themed PyQt6 interface.  The values shown in the GUI come from the **DEFAULT CONFIGURATION** section inside `core.py` and can be adjusted there.

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
python3 plotting/stress_dependence/stress_gui.py
```

## Plotting stress sensitivity data

`plotting/stress_sensitivity/sens_gui.py` visualizes how the switching
times change with applied stress. File names follow the same pattern as for
stress dependence. Each sample is placed on the X axis and a miniature stress
dependence curve is drawn using only the unloading (`b`) data. Raw points and
their means are shown and the difference between 17.5 g and 2.5 g unloading is
annotated for every sample. The plot therefore compares the full stress
behaviour across all samples in a compact layout.

Run the script with:
```bash
python3 plotting/stress_sensitivity/sens_gui.py
```

## Plotting temperature sensitivity data

`plotting/temperature_sensitivity/temp_gui.py` visualizes switching-time
measurements taken at different temperatures. File names must follow:

```
<composition> <sample> <anneal> <temp>C.txt
```

Select multiple measurement files to plot T1, T2, T2–T1 and T1+T2 for all
samples. Raw points recorded at 25 °C are jittered slightly left of the sample
index and those at 100 °C to the right so the two sets do not overlap. Mean
values are drawn centered on the sample index. A thin vertical line connects the
25 °C and 100 °C means for each sample with the numeric difference printed
next to it. The plot can show the raw values, subtract the 25 °C baseline or
generate both variants. Optionally a processed continuous measurement can be
displayed for each sample using adjustable median and moving-average windows.

Run the script with:
```bash
python3 plotting/temperature_sensitivity/temp_gui.py
```

## Plotting temperature dependence data

`plotting/temperature_dependence/temp_dep_gui.py` combines discrete
measurements at 25 °C and 100 °C with a continuous run between those
temperatures. The GUI can display raw points, processed curves or both and
supports plotting the usual T1/T2 derived quantities.

Run the script with:
```bash
python3 plotting/temperature_dependence/temp_dep_gui.py
```

## Comparing Hsw distributions by load

`plotting/hsw_load_compare/load_compare_gui.py` stacks probability density plots for a set of ascending measurement files.  The GUI lets you choose whether to display TT and/or HH curves, show raw data and histograms and keep histogram Y axes shared or independent.

Run the script with:
```bash
python3 plotting/hsw_load_compare/load_compare_gui.py
```

## Plotting Maxion continuous measurements

`plotting/maxion_continuous/maxion_gui.py` visualizes Maxion continuous measurement files and allows plotting raw and/or processed curves for all three channels.  Figures can be displayed and optionally saved.

Run the script with:
```bash
python3 plotting/maxion_continuous/maxion_gui.py
```

## Hsw distribution analysis

`plotting/hsw_distribution/distribution_gui.py` applies a Histogram-Core filter to TT and HH (or T1 and T2) measurements, then plots raw curves, count histograms and probability density curves.  You can choose the column naming scheme in the options dialog.

Launch it with:
```bash
python3 plotting/hsw_distribution/distribution_gui.py
```

## Data logger

`data_logger/data_logger.py` records serial data to a file.  By default logs
are stored in a `python_plot_logs` directory inside the current user's home
folder so the path works on any system.  You can change the location at
runtime with the `--log-dir` option or the `LOG_DIR` environment variable.  The
script also exposes constants for the pre-filled command string and suggested
file name.  Inside the GUI you can browse to a different directory at any time.
The interface uses a dark Fusion palette with rounded buttons for a modern
look.

Launch the logger with:

```bash
python3 data_logger/data_logger.py
```

Use the drop-down boxes to select the serial port and baud rate, then press **Connect to port**.  The **Record** button (or pressing **Enter** in the log-file field) prompts for a file name and stores the log in the directory shown in the *Directory* field.  Log files are always saved with a *.txt* extension.
When *Use subfolder* is enabled the logger creates a directory named after the
selected file (without the load suffix) and stores the log inside it.
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

Default settings for the stress dependence and temperature sensitivity plotters
are stored in `plotting/default_config.json` and can be customized by
providing a modified path to :func:`plotting.config.load_config`.

## Virtual environment

It is recommended to work inside a Python virtual environment to keep
dependencies isolated.  The steps in the *Installation* section show how
to create and activate a virtual environment on both Windows and macOS.
Once activated, install the requirements with ``pip`` and remember to
activate ``.venv`` again whenever you open a new terminal.

## Troubleshooting

Even with the steps above things can occasionally go wrong. The following table
lists the most common problems and how to solve them.

| Problem | Cause | Solution |
|---------|-------|----------|
| ``'python' is not recognized`` | Python was not added to ``PATH`` during installation. | Reinstall Python and select *Add python.exe to PATH* or call it via its full path. |
| ``No such file or directory: requirements.txt`` | The command was run from the wrong folder. | Change into the repository directory that contains ``requirements.txt`` before running ``pip``. |
| ``PermissionError`` when activating ``.venv`` on Windows | PowerShell execution policy blocks scripts. | Run ``Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`` and then activate again. |
| ``pip`` not found or very old | ``pip`` was not installed or is outdated. | Run ``python -m ensurepip --upgrade`` followed by ``python -m pip install --upgrade pip``. |

If you still encounter issues, consider searching the exact error message online
or ask someone familiar with Python for help.

