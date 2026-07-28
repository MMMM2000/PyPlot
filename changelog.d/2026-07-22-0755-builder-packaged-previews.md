2026-07-22 07:55

- Prefer a packaged `*_latest_v3.pydpj` database over the oversized legacy `*_latest.pydpj` during Builder startup auto-open.
- Restore real overview graphs in packaged projects: small graph sections load records in the background, while large VSM-hysteresis and TMA tables decode only visible-row records and progressively replace loading cards with previews.
- Keep skipped legacy pickle stores silent during blank or packaged-project startup; explicit trusted-copy migration remains the only path that reads them.
