2026-05-15 13:23

- Move Mini DMA recipe/control ticks onto a worker scheduler with frozen run-start settings so Qt repaint lag and Matplotlib redraws do not pace hardware control or CSV/control-trace logging.
- Serialize Mini DMA PSU serial access between worker current commands and UI readbacks, correctly parse scientific-notation current replies, add a current-sweep channel selector, and reset the current channel to output off at `1 V` / `1 mA` whenever automation stops.
- Tighten the Mini DMA dashboard header so the current task uses a fixed single-line row, remove the redundant scale-rate cell, lighten live-plot markers/lines, keep older downsampled plot points visually stable, and remember current-sweep target ranges separately for iso-load, iso-stress, and iso-strain modes.
- Include the current-sweep recipe type in auto-generated Mini DMA output base filenames, for example `iso-stress` or `iso-strain`.
- Let Mini DMA setup finish from a stable near-zero plateau during linear-unload fallback instead of waiting indefinitely for an unreachable fitted zero-stress position.
- Stabilize Mini DMA current-sweep task text during worker ticks and keep scheduled CSV rows flowing while iso-strain current sweeps are already inside target tolerance.
- Close/delete Mini DMA setup and recovery child dialogs cleanly and suppress recovery prompts during window shutdown so a completed run cannot leave the main window trapped behind stale dialog ownership.
- Add a constant-current stress-strain recipe that seeks a chosen load/stress/strain start target, then applies fixed open-loop displacement or strain steps up to a target and optionally back down at each configured current, holding/logging after every step without correcting load fluctuations away.
- Remember Mini DMA dashboard plot channel choices separately per recipe type, using the existing global dashboard layout as the fallback for recipes that have not been customized yet.
- Name constant-current Mini DMA output folders with an `iso-current` token, clamp the fixed step-back leg at its remembered mechanical start position, and avoid hidden post-completion origin recovery for that recipe.
- Remove the constant-current stress-strain max-step cap setting and clamp active recipe current commands to at least `1 mA` so continuity/wire-break diagnostics remain powered even when recipe fields are set to `0 mA`.
- Re-zero the constant-current stress-strain scan after each current change, log current-specific zero position, `l0`, and current-relative displacement/strain columns, and use that zero as the step-back origin for the current leg.
- Start setup-preload ramps from the live load/stress value instead of forcing the target clock through zero when the sample is already partly loaded.
- Marshal recipe-completion cleanup and session stop back to the Qt thread so worker-thread completion cannot directly manipulate widgets, timers, or Matplotlib state.
- Add UI telemetry documentation for event-loop heartbeat, live-label cadence, and dashboard graph redraw timing.
