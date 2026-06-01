2026-06-01 14:39

- Mini DMA experiment child processes now write stdout, stderr, and Python faulthandler output to ignored logs under `logs/experiment_processes/`.
- Mini DMA saved Builder project auto-import now runs in the background during startup so a large saved `.pydpj` cannot freeze the initial UI.
- Mini DMA setup plots reserve right-axis space to avoid clipping the load axis in the length setup dialog.
- Mini DMA task summaries now prefer the active long-running recipe step so stress target ramps do not flicker to the next step.
- Mini DMA mid-run current-sweep updates now extend the active current ramp when the edited end current is still safely ahead of the live setpoint, while reporting conservative future-only updates when it is not.
