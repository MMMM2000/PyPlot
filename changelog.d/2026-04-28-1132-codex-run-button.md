2026-04-28 11:32 UTC

- Fixed the Windows Codex `Run` action so it returns to PowerShell after PyPlot exits instead of leaving the terminal parked inside `cmd.exe`.
- Hardened the tracked `run-pyplot.cmd` wrapper so already-cached `cmd /k` Run commands also skip the pause and close cleanly.
