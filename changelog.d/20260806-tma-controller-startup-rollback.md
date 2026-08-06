2026-08-06 UTC

- Report the original dedicated TMA controller bootstrap exception instead of only its exit code.
- Close the pre-run setup graph and restore retryable UI controls when controller startup fails before hardware ownership transfer.
- Prewarm the hardware-free controller while mounted length is entered, and limit isolated ETA/progress display refreshes to once per second unless the task changes.
