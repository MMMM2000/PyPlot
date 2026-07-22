2026-07-03 08:32
- Fixed TMA recipe stops requested by the background control worker so stale scale feedback and related control faults are finalized on the UI thread instead of risking a crash.
- Added an automatic `run_log.txt` mirror inside each TMA run folder so shared run outputs include the run log and stop/fault messages.
