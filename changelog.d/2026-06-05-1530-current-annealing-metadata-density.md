2026-06-05 15:30
- Current Annealing can now load optional `.pydpj` projects and fabrication spreadsheet folders to suggest composition, microwire, and diameter values.
- Current Annealing displays current-density equivalents beside current values and on the top axis of the live resistance-vs-current graph when a diameter is known; the logger hides density values when no diameter is available.
- Fabrication spreadsheet loading now runs in the background so massive folders do not freeze the Current Annealing UI.
- The Current Annealing Microwire field now displays slash-style labels such as `1/2`; generated filenames still use filesystem-safe separators.
- Current Annealing process settings now give current-density readouts their own row space and pin the progress/time estimate strip directly above the run buttons.
