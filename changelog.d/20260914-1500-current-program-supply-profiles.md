### Current Program Logger

- Save connection, rates, electrical limits, logging and recipes independently per supply; migrate legacy values into the last-used mode only and warn when application ceilings reduce them.
- Automatically discover matching VISA resources asynchronously on mode changes; reject stale results, avoid choosing among multiple matching instruments and hide unrelated connection fields.
- Bound Siglent startup baseline qualification at the initial current before advancing a recipe; record raw startup I/V and retain fault shutdowns instead of silently bypassing zero readings.
- Document manufacturer rate evidence and an authorized output-off Siglent USB benchmark. Apply separate control/poll ceilings: Siglent 10/20 Hz; HMP 5/2 Hz (read rate provisional from operator experience); retain Keithley's higher-rate options without promising USB throughput.
