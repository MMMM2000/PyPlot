# Microwire Data Plotting & Logging

A compact toolkit for logging, visualising, and post-processing microwire
experiments. The launcher keeps the utilities together so you can jump between
loggers, plotters, emulators, and builders without starting individual scripts.

## Quick Start

1. Create a virtual environment: `python3 -m venv .venv`
2. Activate it (`source .venv/bin/activate` on macOS/Linux or
   `.venv\Scripts\activate` on Windows)
3. Upgrade pip: `python -m pip install --upgrade pip`
4. Install the runtime stack for every non-experiment tool:
   `pip install -r requirements.txt`
5. (Optional) Install experiment helpers and test tooling:
   `pip install .[test]`
6. Launch the hub: `python -m launcher`

OriginPro users should also install `originpro`, `numpy`, `pandas`,
`python-dateutil`, `pytz`, `six`, and `tzdata` inside Origin’s embedded Python
before choosing the Origin backend.

## Tools

- Master Launcher – browse and start every utility from one window with search
  and recent-history sorting.
- VSM Hysteresis Loops – plot loops by temperature/angle, align endpoints,
  export to Matplotlib figures or Origin workbooks.
- Serial Data Logger – capture instrument output with live plots and flexible
  filename templates.
- Microwire Data Builder – combine fabrication spreadsheets and annealing logs
  into an analysis-ready table.
- Universal Serial Emulator – spin up loopback serial pairs to exercise loggers
  without hardware.

## Building

Create a standalone application with PyInstaller after installing the project
dependencies:

```bash
pyinstaller launcher.spec
```

The build appears under `dist/launcher`; zip that folder when sharing the tools.

## Development

- This repository is developed exclusively with Codex CLI sessions
  (`npm install -g @openai/codex`).
- Detailed change history lives in `CHANGELOG.md`.
