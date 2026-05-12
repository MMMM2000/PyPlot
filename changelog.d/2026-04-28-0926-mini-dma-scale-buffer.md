2026-04-28 09:26

- Mini DMA now separates high-rate scale acquisition from slower session logging, writes a raw `<run>.scale_raw.csv` sidecar during active sessions, and adds interval load summary columns to the main CSV.
- Current-sweep recipes now expose separate control and log intervals so closed-loop corrections can run faster than recorded session rows.
