# Python Plot

This repository contains tools for logging and plotting measurement data.

## Usage

The script `data_plotting/universal_plot.py` plots the text files located in a data directory. By default the script looks in `./data`.

You can specify a custom data directory with the `--data-dir` option or the `DATA_DIR` environment variable:

```bash
# using command-line argument
python3 data_plotting/universal_plot.py --data-dir /path/to/my/files

# or using environment variable
DATA_DIR=/path/to/my/files python3 data_plotting/universal_plot.py
```

The script uses the pattern defined by `GLOB_PATTERN` inside the data directory to select files and then shows the resulting plots.

## Data logger

The GUI logger in `data_logger/main.py` can save measurement output to a
directory of your choice. At the top of the script there is a **USER
CONFIGURATION** section where you can edit the `LOG_DIR` variable to point to
your preferred folder. This location can still be overridden with the
`--log-dir` command line option or the `LOG_DIR` environment variable:

```bash
# using command-line argument
python3 data_logger/main.py --log-dir /path/to/save

# or using environment variable
LOG_DIR=/path/to/save python3 data_logger/main.py
```

Only the log file **name** is entered in the GUI.  The file is saved inside
the directory specified by `LOG_DIR`.  When selecting a file via the "Record"
button the dialog will start in this directory and only the chosen file name is
shown in the text box.
