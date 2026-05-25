2026-05-25 15:58

- Added a shared Windows sleep-prevention guard for active experiments.
- Mini DMA Logger now keeps the PC awake while a session is running and releases the guard when the session stops or the window closes.
- Current Annealing Logger now keeps the PC awake while an annealing process is running and releases the guard during safe shutdown or window close.
