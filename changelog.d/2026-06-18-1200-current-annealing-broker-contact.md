2026-06-18 12:00
- Current Annealing now warns/stops on zero measured current in shared-HMP broker mode instead of silently waiting at startup with an open contact.
- Current Annealing now refreshes shared-broker channel limits before a run and blocks starts where the requested current exceeds the confirmed broker limit, avoiding silent current clamping.
