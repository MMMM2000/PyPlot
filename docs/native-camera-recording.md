# Native camera recording prototype

`scripts/native_camera_recording.py` records native MJPEG packets without
re-encoding the archive. A separate worker decodes selected packets for a live
JPEG preview. Its queue holds only the latest packet; intentional preview
decimation never removes frames from the archive.

This standalone prototype does **not** control a power supply, replace electrical
safety checks, or integrate into the logger UI yet. No heating is needed to test it.

Example (PowerShell, from the repository root; use a new output directory):

```powershell
$env:UV_CACHE_DIR = 'artifacts/uv-cache'
uv run --no-sync --with opencv-python --with imageio-ffmpeg python scripts/native_camera_recording.py --camera "OBSBOT Tiny 3 Lite StreamCamera" --mode 1080p120 --seconds 12 --preview-hz 10 --output artifacts/native-camera-tests/new-run
```

Use `--mode 4k30` for 3840×2160 at 30 fps. Camera capture requires an explicit
`--camera`; `--input <synthetic.mkv>` instead exercises the pipeline without camera
access. `--ffmpeg <absolute-path>` can select an existing FFmpeg executable.
These optional command-line tools are not new mandatory application dependencies.

Outputs:

- `native.mkv`: native archive, with source-derived presentation timestamps.
- `packet-arrivals.csv`: host-monotonic packet delivery times and frame IDs.
- `latest-preview.jpg`: periodically updated sampled preview, not full-rate video.
- `preview-timing.json`: packet arrival and preview decode-completion times.
- `all-frame-analysis.csv`: every archived frame decoded and analysed afterward.
- `analysis-summary.json`: frame-count agreement, rate, gaps, and duplicate checks.
- `capture.log` and `manifest.json`: capture diagnostics and status.

Optional `--roi x y width height` tracks a selected feature against the first
recorded frame. Coordinates refer to **native resolution**. Displacement is in
pixels, not calibrated millimetres; camera movement and background contamination
can affect it. Without an ROI, all frames still receive intensity and timestamp
checks. Offline analysis can be repeated with `--analyze-only --output <run>`.

Native PTS, host arrival time, and preview decode completion are different clocks
or stages. Do not interpret the capture-process start as an exposure timestamp.
The prototype checks recorded-frame completeness, not the camera's sensor frame
counter. Absolute camera latency and electrical/video clock alignment still need
an optical timing reference. It is not a camera-based safety interlock.
