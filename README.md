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
   - Project dependencies and their pinned versions are defined in ``pyproject.toml``.
   - Install the pinned set of packages with ``pip install -r requirements.txt``. Make
     sure you are in the repository folder so ``requirements.txt`` is found.
   - The ``requirements.txt`` file is generated from ``pyproject.toml`` using ``pip-compile``
     from [pip-tools](https://github.com/jazzband/pip-tools). To update dependencies,
     modify ``pyproject.toml`` and run ``pip-compile pyproject.toml``.
   - Alternatively run ``pip install -e .`` to install the project in editable mode.

6. **Start the launcher**
   - Run ``python -m launcher`` to open the graphical tool window.

Reactivate the virtual environment whenever you open a new terminal. Repeat the
``activate`` command from step 4 before running ``python`` or ``pip`` again.

## Building standalone executables

The Python tools can be bundled into native applications with
[PyInstaller](https://pyinstaller.org/).  This dependency is declared in
``pyproject.toml`` and installed via the generated ``requirements.txt`` so no extra setup is required. After activating the virtual
environment and installing the requirements you can create platform specific
packages with the provided `launcher.spec` which bundles required data files.

### Windows (`.exe`)

```bash
pyinstaller launcher.spec
```

The executable `launcher.exe` appears in the `dist` folder and can be copied to
any Windows machine.

### macOS (`.app` or `.dmg`)

```bash
pyinstaller launcher.spec
```

This creates `dist/launcher.app`.  To distribute it as a disk image run:

```bash
hdiutil create -volname PythonPlot -srcfolder dist/launcher.app dist/launcher.dmg
```

Both commands may be executed on a Mac to generate a self‑contained application
bundle.

React or Tauri experiments are **not** required for the Python tools.  They live
in the `experiments` directory and have their own dependencies and build steps.

## Experiments

The ``experiments`` folder holds interface prototypes that are separate from the
core Python code. It currently includes an experimental **data plotter** that
lets you choose a plotting script, select input files and tweak configuration
options in a persistent window. Run it with ``python -m experiments.data_plotter``.

## Master launcher

`launcher.py` starts a small GUI that groups all available loggers and
plotting tools (Stress Dependence, Temperature Sensitivity and others).
Select an item and press **Run** to launch it:

```bash
python -m launcher
```


## Backends: Matplotlib and Origin

All plotting tools can render graphs with Matplotlib, Origin, or both:

- In every plotting dialog, use the new "Backend" selector to choose `Matplotlib`, `Origin`, or `Both`.
- Default is `Matplotlib` so behavior remains unchanged unless you switch it.
- Advanced users can also set the per‑tool `BACKEND` in `plotting/default_config.json` or call the core APIs directly, e.g. `core.main(files, backend="origin")` or `backend="both"`.

Notes on Origin output:
- Origin graphs are generated with the `originpro` Python package and attempt to mirror the Matplotlib look (colors, markers, lines, titles, labels, legend).
- Support is complete for Stress Dependence and added to Temperature Dependence, Temperature Sensitivity, Stress Sensitivity, Hysteresis Loops, and Maxion continuous plots.
- HSW Load Compare currently outputs the ln(dp/dh) vs reduced field panels to Origin; histograms and raw panels stay Matplotlib‑only for now.

## Data logger

The data logger window allows recording measurements and saving them to text
files. A built-in name builder assembles structured file names from individual
fields such as composition, sample number or load. For stress files the
annealing field accepts descriptions like *ascast*, *300C* or *74mA*. When using
the temperature template the measurement temperature can be selected from
**25C**, **25-100C** (a continuous run) or **100C**. To keep file names compatible
with the plotting scripts, input fields reject spaces and hyphens; the sample
number is the sole exception and must contain exactly one hyphen (e.g.
``s2-2``). Alternatively you can type any custom file name directly into the log
file box.


## Plotting stress dependence data

`plotting/stress_dependence/stress_gui.py` generates plots from a folder of measurement files using a PyQt6 interface that follows the system light or dark theme.  The values shown in the GUI come from the **DEFAULT CONFIGURATION** section inside `core.py` and can be adjusted there.

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

Several flags control what variables are plotted and whether the plots are displayed (`SHOW_PLOTS`) or saved (`SAVE_PLOTS`).

## Plotting stress sensitivity data

`plotting/stress_sensitivity/sens_gui.py` visualizes how the switching
times change with applied stress. File names follow the same pattern as for
stress dependence. Each sample is placed on the X axis and a miniature stress
dependence curve is drawn using only the unloading (`b`) data. Raw points and
their means are shown and the difference between 17.5 g and 2.5 g unloading is
annotated for every sample. The plot therefore compares the full stress
behaviour across all samples in a compact layout.


## Plotting temperature sensitivity data

`plotting/temperature_sensitivity/temp_gui.py` visualizes switching-time
measurements taken at different temperatures. File names must follow:

```
<composition> <sample> <anneal> <temp>.txt
```

where `<temp>` is one of **25C**, **100C** or **25-100C** for a continuous run.
Select multiple measurement files to plot T1, T2, T2–T1 and T1+T2 for all
samples. Raw points recorded at 25 °C are jittered slightly left of the sample
index and those at 100 °C to the right so the two sets do not overlap. Mean
values are drawn centered on the sample index. A thin vertical line connects the
25 °C and 100 °C means for each sample with the numeric difference printed
next to it. The plot can show the raw values, subtract the 25 °C baseline or
generate both variants. If a file named **25-100C** is present a processed
continuous measurement can be displayed for each sample using adjustable median
and moving-average windows.


## Plotting temperature dependence data

`plotting/temperature_dependence/temp_dep_gui.py` combines discrete
measurements at 25 °C and 100 °C with a continuous run between those
temperatures. Name the continuous file **25-100C** so it is detected
automatically. The GUI can display raw points, processed curves or both and
supports plotting the usual T1/T2 derived quantities.


## Comparing Hsw distributions by load

`plotting/hsw_load_compare/load_compare_gui.py` stacks probability density plots for a set of ascending measurement files.  The GUI lets you choose whether to display TT and/or HH curves, show raw data and histograms and keep histogram Y axes shared or independent.


## Plotting Maxion continuous measurements

`plotting/maxion_continuous/maxion_gui.py` visualizes Maxion continuous measurement files and allows plotting raw and/or processed curves for all three channels.  Figures can be displayed and optionally saved.


## Hsw distribution analysis

`plotting/hsw_distribution/distribution_gui.py` applies a Histogram-Core filter to TT and HH (or T1 and T2) measurements, then plots raw curves, count histograms and probability density curves.  You can choose the column naming scheme in the options dialog.


## Plotting PDF data

`plotting/pdf_plotter/pdf_gui.py` reads values from PDFs that contain rows with four
semicolon-separated columns: `T1; T2; Force; Strain` (comma or dot decimals). The
GUI keeps plotting options visible so you can tweak settings and the plot updates
in place. Options include Y/X selection (defaults to T1+T2 vs Force), line/marker
styles, colors, sizes, legend control, grid, custom axis labels and title, text
sizes, and saving (format, DPI, output directory, and figure size) with an optional
“save on plot” toggle.

A sample PDF is provided in the `sample_data` folder for quick testing.

## Data logger

`data_logger/data_logger.py` records serial data to a file.  By default logs
are stored in a `python_plot_logs` directory inside the current user's home
folder so the path works on any system.  You can change the location via the
`LOG_DIR` environment variable. The script also exposes constants for the pre-filled command string and suggested
file name.  Inside the GUI you can browse to a different directory at any time.
The interface follows the host operating system's light or dark appearance and
uses rounded buttons for a modern look.


Use the drop-down boxes to select the serial port and baud rate, then press **Connect to port**.  The **Record** button (or pressing **Enter** in the log-file field) prompts for a file name and stores the log in the directory shown in the *Directory* field.  Log files are always saved with a *.txt* extension.
The built-in name builder supports stress and temperature templates and offers
25C, 25-100C or 100C as selectable temperature values.
When *Use subfolder* is enabled the logger creates a directory named after the
selected file (without the load suffix) and stores the log inside it.
The port list shows each device's full description to make selection easier.

## Requirements

This project depends on `PyQt6`, `matplotlib`, `numpy` and `pandas`.
Install the pinned dependencies with:

```bash
pip install -r requirements.txt
```

The ``requirements.txt`` file is autogenerated from ``pyproject.toml`` using
``pip-compile`` and should not be edited manually. To update a dependency,
modify ``pyproject.toml`` and regenerate the file:

```bash
pip-compile pyproject.toml
```

Alternatively the project can be installed in editable mode to make all modules
importable without modifying ``sys.path``:

```bash
pip install -e .
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
| ``No such file or directory: requirements.txt`` | The file was not generated or the command was run from the wrong folder. | Run ``pip-compile pyproject.toml`` to generate it or change into the repository directory that contains ``requirements.txt`` before running ``pip``. |
| ``PermissionError`` when activating ``.venv`` on Windows | PowerShell execution policy blocks scripts. | Run ``Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`` and then activate again. |
| ``pip`` not found or very old | ``pip`` was not installed or is outdated. | Run ``python -m ensurepip --upgrade`` followed by ``python -m pip install --upgrade pip``. |

If you still encounter issues, consider searching the exact error message online
or ask someone familiar with Python for help.
