2026-05-29 11:45
- Current Annealing now reverses or stops immediately at the configured maximum current instead of running the legacy hidden hold-current phase.
- Current Annealing voltage-limit handling now ignores obsolete saved `hold` actions and reverses to zero instead of holding current.
- New Current Annealing metadata no longer writes the obsolete `hold_duration_s` field.
- Removed the dormant Current Annealing hold-current timer and handlers so hidden legacy controls cannot affect runtime behavior.
- Shared HMP output-off now cancels queued current and ramp work for that channel so no scheduled setpoints are applied after an app turns its output off.
