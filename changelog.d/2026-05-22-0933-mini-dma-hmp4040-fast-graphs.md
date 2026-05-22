2026-05-22 09:33

- Mini DMA adds HMP4040 support with auto-detect, 115200 baud defaults, current-sweep CH4, and motor-supply CH3 while keeping channels user-configurable.
- Mini DMA dashboard graphs now default to a 500 ms refresh interval and cache older downsampled history so long runs avoid rescanning the full run on each redraw.
- Mini DMA pyqtgraph tiles now keep the run log compact, leave right-edge breathing room, use less dense/thinner major gridlines, and color Y axes to match their plotted curves.
- Mini DMA pyqtgraph tiles now keep empty top/right axes visible as plain frame lines without tick marks or labels when no data axis is assigned there.
- Mini DMA manual setup now shows a modal progress dialog while Auto-connect hardware probes the motor, scale, and optional motor-supply channel.
- Mini DMA manual Auto-connect hardware now prepares the current-sweep supply channel with the configured voltage limit and starting current while keeping that channel output off, so HMP4040 CH4 does not retain stale front-panel settings.
- Mini DMA dashboard plot widgets now shrink correctly in the available panel height and keep the run log shorter so the lower-right graph stays inside the visible window.
- Mini DMA recovery-to-zero now keeps correcting when the measured load is still above the true zero-load tolerance instead of accepting a backlash-limited residual load.
- Mini DMA pyqtgraph tiles now remove gridlines, disable SI-prefix axis scaling for fixed engineering units, and use thin line+symbol traces.
- Mini DMA recovery/setup plots now reuse the same per-quantity colors as the dashboard, and current-hold resume no longer expands its resume band from noisy transformation data.
- Mini DMA dashboard plot tiles now re-cap their maximum height after window resizes so the lower-right graph cannot spill below the visible panel.
- Mini DMA migrates saved 1000 ms graph refresh settings to the new 500 ms default so older local settings do not silently keep setup/recovery graphs slow.
- Mini DMA load/stress seeking now waits for the filtered scale-control signal to change after a correction before repeating another load/stress move, reducing chatter from stale median/MAD windows.
