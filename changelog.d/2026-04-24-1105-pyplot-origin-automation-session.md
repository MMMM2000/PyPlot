2026-04-24 11:05
- PyPlot live automation sessions now carry long command timeouts through the session bridge, so slow Origin exports can return a clean success response instead of timing out at the controller.
- Automation-triggered shared Origin exports now suppress blocking success dialogs while still logging the export result, preventing offscreen/live-session runs from hanging after graphs are created.
