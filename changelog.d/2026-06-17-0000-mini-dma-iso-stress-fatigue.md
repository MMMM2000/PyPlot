2026-06-17 00:00

- Added a Mini DMA `Iso-stress fatigue` recipe for repeated fixed-stress current up/down cycles, with finite cycle count, descriptive recipe filenames, saved JSON/settings support, and cycle-numbered recipe steps for later drift/wire-break analysis.
- Added an offline `mini_dma_fatigue_learning.py` report tool that groups saved repeated iso-stress runs, excludes non-comparable or too-short data, estimates transformation-current shifts from resistance/strain slopes, and emits review-only JSON/CSV/Markdown priors for future fatigue measurements.
