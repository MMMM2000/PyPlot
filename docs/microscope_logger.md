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

For **Parallel edges**, click two points along the first boundary, then a point on
the opposite boundary. The result is the perpendicular distance between parallel
lines. **Auto edges from guides** uses the same three points to fit a dark band
from multiple intensity profiles. Inspect the overlaid boundaries and quality
warnings. Blur, transparent coatings, taper and reflections can make an apparent
optical edge differ from a physical material interface. Automatic results remain
`Unreviewed`; they do not establish validated core or glass diameters.

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

Objective selection is manual; the app cannot detect the physical nosepiece setting.
When its own source paths have not been set, the app reads TMA's saved project and
fabrication paths without constructing the TMA logger. Its output defaults to a
dedicated `microscope_measurements` folder beside the project, rather than TMA's log
folder. You can choose another output folder before capture.

With **Auto-save captures and measurements** enabled, capture creates a new session
directory containing the original image without display overlays, an initial
`measurements.csv`, and the original capture `session.json`. Subsequent edits create
immutable subfolders under `revisions/`; `latest.json` points to the newest
`session.json` and its sibling `measurements.csv`. Original images are not copied
for every revision, and earlier measurement sets remain available. **Save image
and measurements** saves immediately to the selected output folder.
Calibrated sessions also include the reference images. JSON retains individual
sample/source bindings, image hashes, calibration and acquisition metadata. Unknown
physical widths remain blank in CSV and null in JSON. The app never updates the
source project or fabrication workbooks. A future reviewed Builder import can use
these bindings; it must use the normal Builder writer and publication checks.

## Local command control

The running app exposes a user-only local named pipe, with no TCP service. Commands
run on the application thread and update the same visible image and table:

```powershell
uv run python -m data_logging.microscope_logger.control status
uv run python -m data_logging.microscope_logger.control configure --camera-name CamGCC --objective 4x --composition Ni50Fe27Ga23 --microwire 10/4
uv run python -m data_logging.microscope_logger.control start
uv run python -m data_logging.microscope_logger.control capture
uv run python -m data_logging.microscope_logger.control parallel --a 800 538 --b 1800 529 --c 1300 600
uv run python -m data_logging.microscope_logger.control auto --a 800 538 --b 1800 529 --c 1300 600 --samples 15
uv run python -m data_logging.microscope_logger.control save
```

Coordinates above illustrate one image and must be chosen for the actual boundaries.
Use `configure --output-directory PATH` to choose storage. `load-sources --kind
project` and `load-sources --kind fabrication` read suggestions asynchronously;
fabrication loading reads the selected composition only. With no composition,
it lists composition folders first. Selecting another composition loads its own
suggestions; `--composition NAME` selects a scope explicitly. `--cancel` requests
cancellation of a pending source load.
`status` reports when loading finishes. `request --json FILE` sends a structured
JSON command without shell quoting. `close` releases the camera and closes only
after pending image data has been saved. `start --discard` explicitly permits
discarding an unsaved image; the default requires it to be saved first.

`restore-session DIRECTORY` verifies a saved capture and its latest revision,
restores the original image, sample binding and measurement overlays, and permits
further revisions without overwriting prior results. Preview processing is bounded
to keep the GUI and command interface responsive; captured PNGs retain native pixels.

GUI and CLI normally discover one another automatically within the same worktree.
For an isolated instance started with `--runtime-dir PATH`, pass the same option to
the control command before the subcommand. Native Windows camera/named-pipe access
may be unavailable to a restricted shell; use the ordinary user session.

For an offline demonstration, run:

```powershell
uv run python -m data_logging.microscope_logger.microscope_logger --demo
```
