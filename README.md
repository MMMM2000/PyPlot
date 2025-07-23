# Python Plot Tools

This project contains two utilities:

* `data_logger` – a PyQt5 GUI for logging data from a serial port.
* `universal_plot.py` – a helper script for plotting measurements stored in text files.

## Requirements

* Python 3.10 or newer
* [PyQt5](https://pypi.org/project/PyQt5/)
* pandas
* numpy
* matplotlib

You can install the dependencies with `pip`:

```bash
pip install PyQt5 pandas numpy matplotlib
```

On some systems PyQt5 may require additional system packages. If the wheel
installation fails, consult your package manager (e.g. `apt install python3-pyqt5` on Debian based systems).

## Launching the data logger

To start the logging GUI run:

```bash
python data_logger/main.py
```

This opens a window that lets you select the serial port and start logging. Logged
samples can be written to a file for later analysis.

## Using `universal_plot.py`

The plotting helper resides in `data plotting/universal_plot.py`. Before running it,
edit the configuration block at the top of the file to point `DATA_DIR` and
`GLOB_PATTERN` to your measurements. Then execute:

```bash
python "data plotting/universal_plot.py"
```

One or more matplotlib windows will appear showing the selected variables.
