### Fixed

- Keep project Save actions disabled for the complete background-load lifecycle and recover them reliably after preparation or staged-restore failures.
- Restore temporary headless/dialog-suppression environment settings after launcher automation so a later interactive Builder session still reports project-load failures normally.
- Preserve hidden measurement graphs, reviewed microscope values, microscope `oe` visibility, and imported sample-level fields during Assemble rebuilds, including launcher-driven rebuilds and samples with multiple detail rows.
- Persist user changes to Assemble search/source filters, graph preview visibility, imported-row visibility, `oe` visibility, and measurement graph visibility so reopened projects retain the reviewed working view.
- Avoid delayed annealing transition rendering after its review window has already closed.
