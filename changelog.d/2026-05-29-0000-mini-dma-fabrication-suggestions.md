2026-05-29 09:55

- Mini DMA Logger Sample tab can now connect a fabrication-data folder, index it without blocking the UI, suggest compositions and microwires while typing, and use fabrication diameters as a fallback when the connected `.pydpj` project has no diameter for the selected sample.
- Large fabrication database roots are staged: Mini DMA loads top-level composition folders first, then reads only the selected composition subtree for microwire/diameter suggestions.
- `.pydpj` sample import remains the preferred diameter source when both project and fabrication data are available.
