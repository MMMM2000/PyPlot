2026-04-29 09:34

- Mini DMA review/test windows can now opt out of saving settings, preventing temporary screenshot or diagnostic values from overwriting the user's saved sample, project, output, and dashboard plot selections.
- Mini DMA load/stress target ramps now wait for fresh scale feedback after each motor correction instead of stacking planned motion between balance samples, and setup preload completion now honors the displayed setup tolerance.
- Mini DMA Phase 3 control keeps Tic command state separate from slower status polling, so calibration micro-moves chain from the last commanded target and data logging no longer blocks on a Tic status subprocess for every row.
- Mini DMA calibration now waits for a fresh post-move scale sample before recording forward/reverse points and writes an `insufficient_data` calibration report when a calibration session is stopped before a full report can be computed.
- Mini DMA completed calibrations now seed backlash, stiffness, and noise for closed-loop load/stress seeking; stiffness is rescaled for the current gauge length, target corrections use the estimated load-path sensitivity, and too-small tolerances are raised to the motor/noise resolution floor.
- Mini DMA backlash take-up is tracked separately from specimen displacement, so raw motor travel remains in `raw_position_mm` while logged tensile displacement and strain exclude reversal take-up.
- Mini DMA load/stress seek speed limits now use the scale feedback interval instead of the faster control-timer interval, and setup preload slack take-up can use the configured setup stage speed instead of being capped by the fine preload correction step.
- Mini DMA sample/project/output and dashboard plot selections are saved when they change or when a session starts, and restored custom sample/base filenames are no longer overwritten by auto-naming during startup.
- Mini DMA manual move buttons now use true press-and-hold jog control instead of Qt auto-repeat clicks, so held motion follows the configured manual `mm/s` speed more closely even when a jog tick is delayed.
- Mini DMA Manual Actions now include an auto-connect hardware button for setup moves before starting a recipe.
- Mini DMA pytest coverage now isolates the app's `QSettings` backend from the user's real saved Mini DMA settings, and constructor-supplied test output folders no longer replace the saved output folder on a normal close.
