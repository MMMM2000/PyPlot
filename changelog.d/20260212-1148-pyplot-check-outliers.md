2026-02-12 11:48

- Replaced the PyPlot `Check outliers…` placeholder with a functional worksheet scanner that detects statistical outlier rows (IQR method with z-score fallback on low-spread columns).
- `Check outliers…` now presents a detailed per-worksheet summary and supports removing flagged rows directly from affected worksheets, refreshing open worksheet views and Project Explorer row/column counts.
- Added regression tests for outlier finding and in-place outlier row removal in the PyPlot worksheet model flow.
