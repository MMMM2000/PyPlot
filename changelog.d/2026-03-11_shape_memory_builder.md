2026-03-11 08:43

- Added a Shape Memory Stress/Strain section to Microwire Data Builder with static dual-axis graph previews, visibility controls, and PyPlot/Origin handoff actions.
- Included shape-memory graph columns in Assemble, Compare, and HTML export previews so selected microwires can carry the new measurement set alongside DMA, VSM, and FMR graphs.
- Added interactive shape-memory point picking in the builder preview so double-clicked displacement/load/strain/stress values are stored in dedicated columns and can be included in Assemble exports.
- Added fracture-target picking for shape-memory previews so fracture load/strain/stress can be stored separately from the standard picked values and exported through Assemble.
- Renamed the picked shape-memory value columns to plain `Displacement/Load/Strain/Stress` labels, and renamed the older Strain-section outputs to `Legacy strain` / `Legacy stress (MPa)` to distinguish the workflows.
- Added table search across the Microwire Data Builder sections, including the base data/graph tabs and the custom Current density, Transition temps, and Compare views.
- Microscope `oe` filenames are now treated as separate samples, with a Microscope-tab toggle to show or hide those other-end rows.
