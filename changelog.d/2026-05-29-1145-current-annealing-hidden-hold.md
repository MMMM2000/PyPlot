2026-05-29 11:45
- Current Annealing now reverses or stops immediately at the configured maximum current instead of running the legacy hidden hold-current phase.
- Shared HMP output-off now cancels queued current and ramp work for that channel so no scheduled setpoints are applied after an app turns its output off.
