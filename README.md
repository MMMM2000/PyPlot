# Microwire Data Plotting & Logging

A compact toolkit for logging, visualising, and post-processing microwire
experiments. The launcher keeps the utilities together so you can jump between
loggers, plotters, emulators, and builders without starting individual scripts.

## Quick Start

1. Create a virtual environment: `python3 -m venv .venv`
2. Activate it (`source .venv/bin/activate` on macOS/Linux or
   `.venv\Scripts\activate` on Windows)
3. Upgrade pip and install dependencies: `python -m pip install --upgrade pip`
   then `pip install -r requirements.txt`
4. Launch the hub: `python -m launcher`

OriginPro users should also install `originpro`, `numpy`, `pandas`,
`python-dateutil`, `pytz`, `six`, and `tzdata` inside Origin’s embedded Python
before choosing the Origin backend.

## PaddleOCR setup guide

Replacing Tesseract with PaddleOCR introduces a few additional prerequisites,
especially on Windows where the deep-learning runtime needs Microsoft’s redistributable
libraries. Follow the steps below before running any OCR-powered features in the
Microwire Data Builder.

### 1. Confirm your environment

* **Python** – Use a 64-bit Python 3.10 or newer interpreter. PaddlePaddle does
  not publish 32-bit wheels.
* **CPU instructions** – Paddle’s MKL builds assume AVX support. Most modern
  Intel/AMD CPUs include it; if you are using older hardware, check the CPU
  specifications first.
* **Microsoft Visual C++ Runtime (Windows only)** – Install the "Microsoft Visual
  C++ Redistributable for Visual Studio 2015–2022" from Microsoft’s official
  download page if it is not already present. Paddle’s binaries depend on it to
  load MKL and OpenMP components.

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip setuptools wheel
```

The same commands work on PowerShell or Command Prompt. On macOS replace the
last line with `source .venv/bin/activate` to activate the environment.

### 3. Install PaddlePaddle and PaddleOCR

```powershell
pip install paddlepaddle==3.2.0
pip install paddleocr==3.3.0
```

These two packages pull in all required native dependencies. If you intend to
use an NVIDIA GPU, install the matching `paddlepaddle-gpu` wheel for your CUDA
version instead of the CPU-only `paddlepaddle` package (refer to PaddlePaddle’s
release notes for the correct download link).

### 4. Install the remaining project requirements

With Paddle installed, run the normal dependency sync so the rest of the tools
pick up their packages:

```powershell
pip install -r requirements.txt
```

Because `requirements.txt` also pins `paddlepaddle` and `paddleocr`, pip will
skip reinstalling them when it sees the matching versions.

### 5. Pre-download OCR models (optional)

The first time PaddleOCR runs it downloads detection and recognition models into
`%USERPROFILE%\.paddleocr` (Windows) or `~/.paddleocr` (macOS/Linux). To avoid a
delay during your initial Microwire Data Builder run, you can warm the cache in
advance:

```powershell
python - <<'PY'
from paddleocr import PaddleOCR

ocr = PaddleOCR(use_angle_cls=True, lang="en")
ocr.ocr("sample_data/microscope_overlay.png", cls=True)
print("PaddleOCR model download complete.")
PY
```

Replace the image path with any microscope overlay in your repository. The call
will download the models once and then reuse them across future sessions.

### 6. Verify the installation

Run a lightweight smoke test to confirm Paddle loads correctly:

```powershell
python - <<'PY'
import paddle
from paddleocr import PaddleOCR

print("Paddle version:", paddle.__version__)
ocr = PaddleOCR(use_angle_cls=True, lang="en")
print("OCR ready: ", bool(ocr))
PY
```

If the script prints the Paddle version and "OCR ready: True" without raising
an exception, the environment is ready for the Microwire Data Builder.

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
