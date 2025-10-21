# Changelog

## 2025-10-20

- Updated the temperature sensitivity Origin workflow to release generated
  workbooks automatically so the "Open in Origin" button works reliably from
  PyPlot.
- Improved the current annealing logger to remember multi-loop runs, label log
  files with the loop count, keep discarding the initial zero-resistance sample
  even after restarting, write currents in milliamperes with an explicit header,
  and honour loop counts after reversing early at the 30 V limit.
- Added an experiment utility for batch-converting legacy current annealing log
  folders from amperes to milliamperes.

## 2025-10-19

- Bumped pinned dependencies (matplotlib 3.10.7, numpy 2.2.6 (to satisfy opencv-python) , pandas 2.3.3, plotly 6.3.1, psutil 7.1.1, zeroconf 0.148.0, etc.), raised the runtime floor to Python 3.10, and migrated from `PyPDF2` to the actively maintained `pypdf` 6.1.2 via `pip-compile --upgrade`.
- Persisted the VSM window's maximized state and tightened geometry clamping so the workbench stays put and avoids Qt resize warnings when plots render.
- Wired Object Manager checkbox changes through the shared dispatcher so curve visibility, legends, and undo history stay in sync.

## 2025-10-18

- Fixed the Object Manager toggles so hiding a curve updates the plot and any
  field-direction overlays immediately.
- Simplified VSM plot titles to show the formatted sample name (with subscripts
  and sample ratios) alongside the temperature only.
- Normalised sample labels such as `Ni50Fe27Ga23 5-4` so digits render as
  subscripts and the wire number shows as a slash (`Ni50Fe27Ga23 5/4`).
- Prevented a crash triggered by undocking and re-docking the Project Explorer
  by deferring dock rearrangement until Qt finishes the move.
- Streamlined the README and moved detailed release notes into this changelog.
- Added cascade/tile options to the Window menu to manage open graph and
  workbook windows.
- Introduced a manual workbook editor with create/add/delete/reorder column
  capabilities and persistent import folder history when "Keep File Selections"
  is enabled.
