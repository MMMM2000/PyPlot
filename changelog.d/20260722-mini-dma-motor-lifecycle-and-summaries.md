Mini DMA/TMA motor control now serializes target acknowledgement with the Tic
command owner, applies the shared 1/8-step T500 profile with a transport-safe
watchdog, prevents duplicate app processes from owning one Tic, fences stale
commands during pause/stop, and confirms halt/zero operations by their exact
command result.

Long-run diagnostics and summaries are now durable: per-run logs are append-only,
metadata and derived artifacts use atomic replacement, malformed trace rows are
reported without aborting analysis, large traces are processed with bounded
memory, and summary generation records a persistent lifecycle status.
