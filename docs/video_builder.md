# Universal Video Builder

Universal Video Builder is a dedicated manual workflow for linking fabrication spreadsheets and fabrication videos in one place without the rest of the Microwire Data Builder tabs.

## Purpose

- Connect one or more fabrication root folders and scan them recursively for fabrication spreadsheets and video files.
- Show one unified spreadsheet with fabrication baseline columns, linked video paths, and manual review fields.
- Let users review rows manually by opening videos and typing the missing values themselves.
- Save and reopen work as `.pydpj` projects using the dedicated `MicrowireVideoBuilder` project kind.

## Workflow

1. Open `Universal Video Builder` from the PyPlot launcher under `Builders`.
2. Connect a fabrication folder.
3. Refresh fabrication data to build the available row set.
4. Use the add-microwire controls above the table when you want to jump to or re-add specific rows:
   - `Composition` is searchable and narrows while typing.
   - `Draws` opens a scrollable multi-select list that stays open while you choose several draws.
   - `Piece` is optional; leaving it on `All pieces` expands the selected draw(s) into explicit piece rows.
5. Select a row and open its linked video(s) or the matching fabrication spreadsheet(s).
6. If you add rows by mistake, select them in the table and use `Remove selected row(s)` to drop them from the current working table without rescanning the connected folder.
7. Enter missing values manually in the review popup or directly in the table.

## Data Rules

- This builder is manual only. It never runs OCR and does not depend on `extract_video_metrics`.
- Spreadsheet values are the fabrication baseline.
- Piece-placeholder tails are filtered out so the builder does not show high piece numbers that only exist as empty workbook placeholders.
- Video review fills or overwrites the shared fabrication/video fields in the single table.
- Rows with no linked video stay red.
- Missing required review values on rows that do have a linked video show as an amber review state instead of a hard error state.
- First-time fills turn green.
- Overwrites turn amber.
- `Notes` stays neutral and is not treated as a required field.
- `Video end length (m)` is shared across rows of the same draw and drives the derived video piece-length column.

## UI Notes

- The builder uses a single-window layout with the searchable add-microwire controls above the unified review table.
- The visible table keeps fabrication diameter columns (`d`, `D`, `d/D`) when they are available from the fabrication spreadsheets.
- The layout is intentionally compact so the table stays the main workspace, while guidance text and status messages remain readable without overlapping the controls.
- The compact review popup uses the same manual workflow as the existing video tools and is sized to keep the editable row readable.

## Project Format

- File extension: `.pydpj`
- Project kind: `MicrowireVideoBuilder`
- Version: `1`

Projects store the connected roots, the current unified table, the available scanned rows used by the add-microwire workflow, and the manual override/history state needed for the review colours and restore actions.
