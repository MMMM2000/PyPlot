# Changelog

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
