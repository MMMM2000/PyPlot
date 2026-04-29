2026-04-29 09:34

- Mini DMA review/test windows can now opt out of saving settings, preventing temporary screenshot or diagnostic values from overwriting the user's saved sample, project, output, and dashboard plot selections.
- Mini DMA load/stress target ramps now wait for fresh scale feedback after each motor correction instead of stacking planned motion between balance samples, and setup preload completion now honors the displayed setup tolerance.
- Mini DMA Phase 3 control keeps Tic command state separate from slower status polling, so calibration micro-moves chain from the last commanded target and data logging no longer blocks on a Tic status subprocess for every row.
- Mini DMA calibration now waits for a fresh post-move scale sample before recording forward/reverse points and writes an `insufficient_data` calibration report when a calibration session is stopped before a full report can be computed.
- Mini DMA sample/project/output and dashboard plot selections are saved when they change or when a session starts, and restored custom sample/base filenames are no longer overwritten by auto-naming during startup.
