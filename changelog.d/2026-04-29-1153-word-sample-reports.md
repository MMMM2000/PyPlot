2026-04-29 11:53
- Added Assemble Word sample reports that write one `.docx` per sample and embed generated Origin graph objects as editable Word OLE objects when Microsoft Word automation is available.
- Word reports now appear as an Assemble export format and automatically request Origin plot generation so available graph objects can be embedded.
- Added a non-GUI `launcher.py --microwire-word-report` path for exporting sample Word reports directly from a Builder project, assembled workbook, or R vs T CSV.
- Expanded the sample-report template with Assemble sample/fabrication/functional fields, microscope dimensions/images, and fixed graph sections for current annealing, R vs T, VSM, DMA, shape-memory, and FMR measurements.
- Project-based Word report exports now merge saved Builder section rows directly and discover sibling `RvsT` CSVs, so sample reports can include project measurements even when the saved Assemble table is stale.
- Project-based Word report exports now reuse PyPlot/Origin graph generation for available graph families and keep live Origin sessions attached long enough for Word to paste editable Origin OLE objects.
- Assemble Word exports now also route available VSM, DMA, shape-memory, and FMR records through the reusable PyPlot/Origin graph export path before embedding Word OLE objects.
