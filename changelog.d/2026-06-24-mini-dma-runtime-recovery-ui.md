2026-06-24 13:00

- TMA runtime current-sweep edits now reject a refused current-limit refresh without stopping the active recipe, and shared-broker limit changes roll back when the broker refuses them.
- TMA displacement-to-zero recovery now stops after reaching the displacement target instead of adding a timed settle step.
- TMA progress text no longer offsets the progress bar fill, and microwire completion closes after selecting a wire.
