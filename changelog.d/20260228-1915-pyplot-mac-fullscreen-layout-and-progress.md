2026-02-28 19:15 UTC
- PyPlot MDI behavior: switching between graph/workbook subwindows now preserves maximize/fullscreen state on macOS instead of dropping back to windowed mode.
- PyPlot graph sizing: activating or resizing shared MDI subwindows now re-fits Matplotlib figure layout to the active canvas, reducing large empty regions after fullscreen/arrangement/tab-switch transitions across plugins (including VSM Temperature Scan).
- Tight-layout warning dialog now supports applying the selected action (keep sizes, auto-fit, or plugin override) to all affected graphs in the current batch.
- Current Annealing plugin plotting now updates the shared status-bar task progress (`_begin_task_progress` / `_update_task_progress` / `_end_task_progress`) during graph generation.
- macOS UI polish: toolbar/tab control buttons now use more native behavior/icons (platform default disabled styling, native titlebar glyphs for tab hide/close controls, and mac-friendly toolbutton raise behavior).
