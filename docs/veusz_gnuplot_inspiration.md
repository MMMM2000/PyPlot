# External Inspiration: Veusz & Gnuplot

## Veusz
- **Composable plot widgets** that are assembled into documents, which suggests we could expose a similarly modular “layout list” to the user instead of forcing just X/Y panes.
- **Dataset editing and capture** (live imports from sockets and CSVs) shows how to treat imported tables as first-class objects across the UI, including metadata, filters, and interactive pickers.
- **Extensible plugin API, scripting, and DBUS interfaces** prove it pays off to decouple GUI calls from the underlying engines, so we might mirror that by keeping every plotter script isolated behind a plugin façade that talks to shared helpers.
- **Publication-ready export pipelines** (PDF/SVG/PNG with color maps, error bars, labels, multiple axes) remind us to preserve axis/theme metadata while plotting so downstream exporters (Origin, Matplotlib) can stay faithful.
- **Interactive tutorials and data filtering tools** hint at useful enhancements such as inline helpers, filter presets, or tooltips that highlight how to interact with the plot sections.

## Gnuplot
- **Command-driven workflow** emphasizes a declarative configuration (plot → style → terminal) that we could emulate with saved “plot recipes” or script templates accessible from PyPlot’s UI.
- **Wide terminal/export ecosystem** (PDF/EPS/PNG/interactive X11) underscores the value of separating the render target from the data processing path, which can help us keep the Origin/Matplotlib exporters lean.
- **Portable build & interface layers** suggest we keep our dependencies modular (e.g., the launcher vs. plugin backends) so new plot engines can plug in without reworking the entire UI.
- **Built-in help/histories** show how interactive prompts and command histories guide users, which aligns with the idea of surfacing log/tooltips and Test/Run outputs under the “Testing”/“Message Log” panels.

## Opportunities
- Provide short “recipe” documentation for each plugin, similar to gnuplot’s command sheets, so users can reproduce what the GUI does via scripts.
- Explore streaming or event-driven data captures (Veusz) so long-running acquisitions can feed into PyPlot without manual imports.
- Consider a plugin discovery panel that mirrors Veusz’s widget catalog—grouping similar plot types and exposing the metadata required to reproduce them elsewhere.
