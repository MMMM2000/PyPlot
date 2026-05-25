2026-05-25 10:42

- Changed Mini DMA current-sweep plots to use line+symbol curves by default.
- Kept PyPlot-style titles, axis labels, and legends in the Microwire Data Builder current annealing graph display.
- Embedded parsed graph payloads in Microwire Data Builder `.pydpj` project saves so copied projects can restore graph records without depending on the global Builder cache.
- Added the first copy-safe Microwire Builder automation recipe path for updating VSM temperature scan sections in copied `.pydpj` projects.
- Suppressed automatic recursive pending-file scans during mini-database section construction to reduce launch-time stalls when saved sections point at large folders.
