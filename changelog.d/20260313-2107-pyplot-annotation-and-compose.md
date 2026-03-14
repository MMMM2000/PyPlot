2026-03-13 21:07
- Added a shared PyPlot annotation toolbar with text, arrow, line, rectangle, and ellipse tools, plus Object Manager integration so annotation objects can be selected, shown/hidden, recolored, and deleted after placement.
- Expanded the shared format toolbar for graph text and shape editing with font-family selection, stroke width, fill colour for shapes, and mathtext helpers for subscript/superscript text editing.
- Added `File -> New -> Compose Graph...` to overlay visible series from existing plotted tabs into a new composed graph tab, plus a worksheet-backed `Create Graph...` builder for choosing exact X/Y columns and legend labels when creating new graphs.
- Persisted manual/composed graphs and their annotation objects in `.pypj` projects so the extra layout/annotation work survives reopen.
