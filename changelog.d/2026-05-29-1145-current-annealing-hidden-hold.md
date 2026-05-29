2026-05-29 11:45
- Current Annealing now reverses or stops immediately at the configured maximum current instead of running the legacy hidden hold-current phase.
- Current Annealing voltage-limit handling now ignores obsolete saved `hold` actions and reverses to zero instead of holding current.
- New Current Annealing metadata no longer writes the obsolete `hold_duration_s` field.
- Removed the dormant Current Annealing hold-current timer and handlers so hidden legacy controls cannot affect runtime behavior.
- Removed the obsolete hidden Current Annealing hold-current UI widgets instead of keeping invisible compatibility controls in the process panel.
- Current Annealing metadata now preserves decimal current ramp rates such as `0.2 mA/s` instead of truncating them to integer `step_mA` values.
- Shared HMP output-off now cancels queued current and ramp work for that channel so no scheduled setpoints are applied after an app turns its output off.
