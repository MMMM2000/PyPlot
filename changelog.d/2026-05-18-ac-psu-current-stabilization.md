2026-05-18 15:45 UTC

- Changed AC microwire current sweeps to wait briefly for PSU actual-current readback after setting each current point before starting LCR reads.
- Kept zero-current/dropout readback as an abort condition after the current has been accepted, so open-circuit or broken-wire failures still stop the run.
- Made AC PSU shutdown set current and voltage to zero before turning output off.
