# Python Plot

This repository contains simple tools for logging measurement data and plotting the resulting text files.

## Plotting stress dependence data

`data_plotting/stress_dependence_plot.py` generates plots from a folder of measurement files.  The script presents a Tk-based dialog to pick the input files and configure options interactively.  The values shown in the GUI come from the **DEFAULT CONFIGURATION** section at the top of the file and can be adjusted there.  A PyQt6 version with a dark theme is available at `pyqt6_plotting/stress_dependence_plot.py`.

- `DATA_DIR` – directory where your raw `.txt` files live
- `OUTPUT_DIR` – directory in which to save generated plots
- `GLOB_PATTERN` – wildcard pattern for selecting files

Measurement file names must follow:

```
<composition> <title> <sample_end> <anneal> <load><dir>.txt
```

For example `FeSiBP 188_1 s4-2a 68mA 10a.txt`.

Several flags control what variables are plotted and whether the plots are displayed (`SHOW_PLOTS`) or saved (`SAVE_PLOTS`).  After editing the configuration simply run:

```bash
python3 data_plotting/stress_dependence_plot.py
```
The PyQt6 version is started with:
```bash
python3 pyqt6_plotting/stress_dependence_plot.py
```

## Comparing Hsw distributions by load

`data_plotting/Hsw_load_compare.py` stacks probability density plots for a set of
ascending measurement files.  A small options window lets you choose whether to
display TT and/or HH curves as well as raw data and histograms.  Additional
controls allow saving the generated figures and keeping histogram Y axes shared
or independent.  Loads are sorted from lowest to highest for easy comparison.
A PyQt6 variant with dark-mode UI lives in `pyqt6_plotting/hsw_load_compare.py`.

Run the script with:

```bash
python3 data_plotting/Hsw_load_compare.py
```
The PyQt6 version is started with:
```bash
python3 pyqt6_plotting/hsw_load_compare.py
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

## Requirements

The plotting script depends on `numpy`, `pandas`, `matplotlib` and `tqdm` for
displaying progress bars.  The logger requires `PyQt6` and `pyserial`.
Install the dependencies with:

```bash
pip install numpy pandas matplotlib tqdm PyQt6 pyserial
```

