2026-05-13 14:38
- Added an overnight AC susceptibility sweep mode that measures selected LCR models, frequencies, and excitation levels across configurable current loops.
- Added OWON SPE6102-compatible current-source support alongside the HMP4030-style SCPI path, with safe output-off handling on stop or error.
- Switched the AC susceptibility default toward `Ls-Rs` while keeping optional `Lp-Rp` measurements and LCR-only baseline capture.
