2026-05-26 12:35

- Fixed Mini DMA current-sweep voltage-limit recovery so unwind ramps back from the measured supply current if internal setpoint state is missing, preventing an instant jump back to the sweep start current.
- When a current sweep is already paused for target recovery, voltage-limit detection now keeps the held current instead of overriding the hold with unwind.
- Moved Mini DMA wire-break stop/recovery prompts onto the UI thread so a wire break cannot freeze the app by opening recovery UI from the control worker.
