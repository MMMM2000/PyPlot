# Python Plot

This repository contains simple tools for logging measurement data and plotting the resulting text files.

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

`pyqt6_logger/main.py` records serial data to a file.  At the
top of the script you can set the default `LOG_DIR` path as well as the
pre-filled command string and suggested log file name.  `LOG_DIR` can also be
overridden via the `--log-dir` command line option or the `LOG_DIR` environment
variable.  Inside the GUI you can change the directory in which logs are stored
using the **Browse** button next to the *Directory* field.  The interface uses a
dark Fusion palette with rounded buttons for a modern look.

Launch the logger with:

```bash
python3 pyqt6_logger/main.py
```

Use the drop-down boxes to select the serial port and baud rate, then press **Connect to port**.  The **Record** button prompts for a file name and stores the log in the directory shown in the *Directory* field.
The port list shows each device's full description to make selection easier.

## Requirements

The plotting script depends on `numpy`, `pandas`, `matplotlib` and `tqdm` for
displaying progress bars.  The logger requires `PyQt6` and `pyserial`.
Install the dependencies with:

```bash
pip install numpy pandas matplotlib tqdm PyQt6 pyserial
```

