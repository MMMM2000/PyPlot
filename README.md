# Python Plot

This repository contains tools for logging and plotting measurement data.

## Usage

The script `data plotting/universal_plot.py` plots the text files located in a data directory. By default the script looks in `./data`.

You can specify a custom data directory with the `--data-dir` option or the `DATA_DIR` environment variable:

```bash
# using command-line argument
python3 'data plotting/universal_plot.py' --data-dir /path/to/my/files

# or using environment variable
DATA_DIR=/path/to/my/files python3 'data plotting/universal_plot.py'
```

The script uses the pattern defined by `GLOB_PATTERN` inside the data directory to select files and then shows the resulting plots.
