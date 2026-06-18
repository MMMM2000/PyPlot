2026-06-18 15:00
- Moved Current Annealing recipe controls into a Recipe tab and advanced broker/HMP controls into a Hardware tab while keeping recipe settings editable before hardware connection.
- Added a Current Annealing plot configuration dialog for the live dashboard, including bottom/left/top/right axis choices for both plots.
- Current Annealing Replace now moves the previous output file and metadata sidecar to Trash or a safe replacement backup before writing the new run.
- Current Annealing now reports hardware auto-connect failures immediately and uses `A/mm²` in visible current-density labels.
- Moved voltage-limit behavior into the Hardware tab and made current sweeps always reverse to zero after reaching the configured maximum current.
- Added an Update running recipe control so automatic Current Annealing runs can apply safe mid-run edits to max current, start current, ramp rate, and loop settings.
