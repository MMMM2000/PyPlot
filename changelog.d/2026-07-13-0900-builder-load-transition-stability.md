2026-07-13 09:00

- Kept normal Microwire Data Builder project opens responsive by moving project read, JSON parsing, and embedded payload decoding off the GUI thread, matching startup auto-open behavior.
- Deferred hidden Annealing, VSM, and TMA transition workspace refreshes until the user opens them, reducing startup and project-load stalls while retaining reviewed states.
- Preserved the active VSM scan across transition workspace refreshes.
