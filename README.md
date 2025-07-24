# Python Plot

This repository contains simple tools for logging measurement data and plotting the resulting text files.

## Plotting stress dependence data

`data_plotting/stress_dependence_plot.py` generates plots from a folder of measurement files.  The script does not provide command line options; instead edit the **USER CONFIGURATION** section at the top of the file to match your setup:

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

## Data logger

The GUI logger under `data_logger/main.py` records serial data to a file.  At the
top of the script you can set the default `LOG_DIR` path as well as the
pre-filled command string and suggested log file name.  `LOG_DIR` can also be
overridden via the `--log-dir` command line option or the `LOG_DIR` environment
variable.

Launch the logger with:

```bash
python3 data_logger/main.py
```

Use the drop-down boxes to select the serial port and baud rate, then press **Connect to port**.  The **Record** button prompts for a file name and stores the log inside `LOG_DIR`.

## Requirements

Both scripts require Python 3 with `numpy`, `pandas`, `matplotlib` and `PyQt5` installed:

```bash
pip install numpy pandas matplotlib PyQt5
```

