2026-05-25 09:35

- AC Susceptibility Logger now keeps Windows awake during active microwire current sweeps and retries PSU shutdown by reopening the selected serial port if the existing handle fails, so error paths still attempt to zero current, zero voltage, and turn output off.
- AC Susceptibility live plots now retain and render a smaller recent preview instead of redrawing thousands of old rows, keeping long overnight sweeps responsive while preserving complete TSV output.
