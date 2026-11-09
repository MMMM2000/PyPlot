# Microscope Diameter

Open **Loggers → Microscope Diameter** in the PyPlot launcher. The camera is opened
only when you select it and press **Start preview**. Close S-Viewer or other camera
applications before starting a preview. **Capture frame to measure** freezes the
image and releases the camera. You can also open a saved microscope image.

Load a Builder `.pydpj` project and/or a fabrication folder, then choose a composition
and microwire/piece. These sources are read-only. Project bindings take priority
when both sources identify the same sample, and all matching bindings are retained.
You can type an identity when no source is available. Source loading runs outside
the interface thread. Every measurement snapshots its own sample selection.

Click two opposite edges, perpendicular to the wire axis. Mouse-wheel zoom changes
only the display; measurements use original image coordinates. Select whether the
line represents an apparent width, outer diameter or metal-core width. That label
does not establish the optical identity of a boundary through transparent glass.

Measurements work in pixels without calibration. To obtain micrometres, select
**Draw reference length**, click the ends of a known distance, enter its length in
µm and describe the reference, then press **Calibrate using reference line**. A stage
micrometer is the preferred reference; another suitably verified known length can
also establish scale. Objective magnification alone does not establish µm/pixel.

Set the objective and acquisition digital zoom to match the microscope. Calibration
profiles are specific to camera identity, image resolution, objective and acquisition
zoom. For imported images with an unknown camera, a profile applies only to the same
image pixels. Recalibration affects new rows; existing rows retain their original
calibration. The original reference image, reference endpoints and SHA-256 are saved
with the profile. Focus, optical halos, edge selection and the reference uncertainty
still limit accuracy.

**Save image and measurements** creates a new session directory containing the
original image without display overlays, `measurements.csv`, and `session.json`.
Calibrated sessions also include the reference images. JSON retains individual
sample/source bindings, image hashes, calibration and acquisition metadata. Unknown
physical widths remain blank in CSV and null in JSON. The app never updates the
source project or fabrication workbooks. A future reviewed Builder import can use
these bindings; it must use the normal Builder writer and publication checks.

For an offline demonstration, run:

```powershell
uv run python -m data_logging.microscope_logger.microscope_logger --demo
```
