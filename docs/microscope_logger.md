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

Three enlargement settings have different meanings: the physical objective changes
the optics, the microscope's digital zoom may crop/enlarge the acquired image, and
the app's mouse wheel changes only the displayed view. **View N%** reports display
scaling; it is not optical magnification and does not change saved pixels or scale.
**Acquisition zoom (manual)** records the actual device setting; it does not read or
operate the microscope's zoom. Keep device digital zoom fixed while calibrating and
measuring. The calibrated µm/pixel value is the scale used for measurements.

Calibration profiles are written immediately after their reference PNG is retained.
The app checks that reference before reusing a saved scale. **Open saved session…**
restores the image, latest rows and calibration together; opening `image.png` alone
does not restore the session. A restored valid calibration also becomes available
for subsequent captures with the same acquisition settings.

## First calibrated measurement session

1. Close S-Viewer and other microscope instances. Choose CamGCC, the intended
   native resolution, and the actual objective. Keep acquisition zoom fixed. The
   objective and acquisition zoom fields are manual declarations, not hardware
   controls; update them to match the microscope.
2. Choose a permanent output folder and leave auto-save enabled. Select the
   composition and wire before collecting specimen measurements. The micrometer
   calibration capture should be clearly identified as a reference, not as a wire
   diameter measurement.
3. Focus the micrometer and capture it. Confirm its graduation spacing and units;
   for example, 0.01 mm is 10 µm. Draw between well-defined graduation centres over
   a long known span, enter that span in µm, identify the reference, and calibrate.
   Calibrate each objective and acquisition mode you intend to use separately.
4. Check a different known span that was not used to set the scale. Check horizontal
   and vertical spans and avoid relying on the extreme field edges. Repeat after
   closing and reopening the app to verify the selected profile is recovered.
   These checks test scale/reuse, not the physical accuracy of a wire edge.
5. Replace the reference with the wire, refocus, capture, and confirm **Calibrated**
   status and the correct sample. Start with manual parallel boundaries on a straight
   section. Repeat the placement at the same location to assess repeatability, then
   measure several locations separately to examine real diameter variation. Inspect
   focus and both boundaries before accepting automatic fits. Treat outer and core
   widths as different measurements; a core label does not validate a transparent
   glass/metal interface.
6. Save, then use **Open saved session…** to check that the original image, sample,
   µm values, geometry and reference evidence return. Read the latest JSON/CSV pair
   via `latest.json`. Measurements made before calibration remain pixel-only; take
   new measurements after calibration rather than assuming old rows are converted.

The scalar calibration assumes the same physical scale in both pixel directions.
If horizontal and vertical checks disagree beyond the required measurement
tolerance, resolve that difference before measuring tilted wires. The required
tolerance must come from the experiment; displayed decimal places are not a
measurement uncertainty. Calibration principles are described in
[Nikon's micrometry guide](https://www.microscopyu.com/microscopy-basics/linear-measurements-micrometry).

For glass-coated microwires, the coating can act as a cylindrical lens. An inner
band viewed through glass can therefore be an apparent core width even after a
correct stage-micrometer calibration. Validate core measurements against an already
exposed core or a documented cross-section/reference method before treating them
as physical metal diameters. Do not apply an assumed refractive-index correction.
This optical effect is described in the
[Bell Labs fibre-core metrology disclosure](https://patents.google.com/patent/US4362943A/en).

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
physical widths remain blank in CSV and null in JSON. Diameter values are not yet
written back to Builder; calibration and a reviewed diameter import remain separate
work. Fabrication workbooks are read only.

## Brittle sample annotation

The **Brittle** checkbox has three states: checked (**yes**), unchecked (**no**),
and a dash (**not assessed**). Saved project annotations populate the selection
when available. An unmarked legacy project cell is unknown, rather than evidence
of a non-brittle specimen. The setting belongs to the selected composition and
microwire, so switching samples does not carry another sample's observation over.

The current selection is saved under `metadata.sample.brittle` in `session.json`.
Each new measurement snapshots `sample.brittle` and `brittle_observed_utc`; CSV
has corresponding `brittle` and `brittle_observed_utc` columns. Unknown is JSON
`null` and a blank CSV cell. Existing measurement rows keep their earlier state;
later changes appear in the session metadata and subsequent rows/revisions.
Project update receipts are retained in session metadata after publication.

To update `.pydpj`, select **Save Brittle to project…**. The app prepares a separate
candidate, verifies all package entries and unrelated payloads, opens it in Builder
with isolated storage, and checks the normal TMA table import. The review shows the
exact sample and changes before **Save** publishes it using the guarded project
publication script, the expected old SHA-256, and a new backup. A stale source,
edited candidate, or active save lock causes refusal. A not-assessed state never
changes the project. Preparing/verifying/saving runs in a worker so the interface
and CLI remain responsive; wait for the operation before closing the app.

The public Builder cell is `Brittle = "brittle"` for yes and blank for no.
An internal boolean `_brittle_override` preserves an explicit no when opening a
legacy glass-only image set that would otherwise infer brittleness. Only the exact
composition/wire identity is updated, including its matching assembled summary.
Known specimens without microscope history gain an annotation-only microscope row.
Project writeback currently uses the Python/uv version of PyPlot; packaged frozen
executables show an explicit unsupported-worker message before creating a candidate.

## Output folders and names

Choose the output folder in the app or with `configure --output-directory PATH`.
The proposed default is `microscope_measurements` beside the Builder project.
Each new capture gets one flat, self-contained folder:

```text
<output>/
  Ni50Fe27Ga23__10_4__20261109T135657347713Z_a5220a09/
    image.png
    session.json
    measurements.csv
    reference_<sha256>.png           # when calibrated
    latest.json                     # after the first revision
    revisions/
      <revision-id>/
        session.json
        measurements.csv
  project_updates/
    <UTC-time>-<unique-id>/
      candidate.pydpj
      review.json
      verification.json
      verification-request.json
      verification-environment/
      verification-stdout.txt
      verification-stderr.txt
      original.pydpj                # backup, created on publication
      publication_receipt.json     # publication result
      publication_receipt.json.stdout.txt
      publication_receipt.json.stderr.txt
```

The folder name is `composition__wire__UTC-time_unique-suffix`. A slash in `10/4`
becomes `10_4` for Windows; unsupported filename characters are replaced and very
long labels shortened. Exact sample labels remain in JSON/CSV. Missing identity
uses `Unassigned`. A new unique suffix keeps repeated captures separate. Renaming
a selection does not rename an existing capture folder; its original manifest
and latest revision show the historical and current selections. Existing folders
created by earlier versions remain readable and are not moved.

`image.png` is the original capture without drawn overlays. `session.json`
contains sample identity, source bindings and hashes, acquisition settings,
annotations, reference evidence and measurement geometry. `measurements.csv` is
the convenient spreadsheet export. After edits, read `latest.json` to find the
current JSON/CSV pair; earlier versions stay available. Original images and
calibration references are stored once per capture, not copied per revision.

## Local command control

The running app exposes a user-only local named pipe, with no TCP service. Commands
run on the application thread and update the same visible image and table:

```powershell
uv run python -m data_logging.microscope_logger.control status
uv run python -m data_logging.microscope_logger.control configure --camera-name CamGCC --objective 4x --composition Ni50Fe27Ga23 --microwire 10/4
uv run python -m data_logging.microscope_logger.control start
uv run python -m data_logging.microscope_logger.control capture
uv run python -m data_logging.microscope_logger.control calibrate --start 200 200 --end 1200 200 --known-um 500 --source "Synthetic example; replace with the actual micrometer span"
uv run python -m data_logging.microscope_logger.control parallel --a 800 538 --b 1800 529 --c 1300 600
uv run python -m data_logging.microscope_logger.control auto --a 800 538 --b 1800 529 --c 1300 600 --samples 15
uv run python -m data_logging.microscope_logger.control save
```

Sample annotation and guarded project update are also available without computer
use:

```powershell
uv run python -m data_logging.microscope_logger.control configure --brittle yes
uv run python -m data_logging.microscope_logger.control prepare-brittle-update
uv run python -m data_logging.microscope_logger.control status
# Inspect project_annotation.review; use its candidate_sha256 for publication.
uv run python -m data_logging.microscope_logger.control publish-brittle-update --review-sha256 <reviewed-candidate-sha256>
uv run python -m data_logging.microscope_logger.control status
```

Use `--brittle no` for an explicit negative observation or `--brittle unknown` to
record not assessed locally. `prepare-brittle-update --workdir PATH` overrides the
review output folder; `--expected-sha256 HASH` supplies the exact source version
when the sample catalogue has not been loaded. Both project commands start jobs;
poll `status` until `project_annotation.state` is `ready`, `published`, or `failed`.

Coordinates and reference length above are examples and must be replaced with the
actual image positions and verified micrometer interval. `status` reports the
active calibration, µm/pixel and reference evidence after calibration.
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
