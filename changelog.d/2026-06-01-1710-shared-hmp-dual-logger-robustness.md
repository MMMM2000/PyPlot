2026-06-01 17:10
- Made shared HMP broker clients use a longer configurable request timeout so dual logger runs are less likely to abort while queued PSU requests are being served.
- Made Current Annealing shared-broker measurements retry one transient missing readback before treating the PSU as unresponsive during concurrent broker use.
