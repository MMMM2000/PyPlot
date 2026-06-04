2026-05-18 16:25

- Add PSU resistance and power readback columns to AC susceptibility microwire sweep logs so current-path behavior is visible alongside LCR values.
- Base microwire sweep progress on the planned setting/current/time position instead of raw elapsed time so communication overhead does not make the progress bar show 100% and ETA 0s while the sweep is still running.
