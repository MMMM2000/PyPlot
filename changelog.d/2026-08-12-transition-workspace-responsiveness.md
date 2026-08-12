- Keep the Builder responsive while transition source paths are checked, avoid
  rebuilding clean Annealing/VSM/TMA overview tabs, and precompute VSM cycle
  splitting in the existing background project loader.
- Preserve visible loading feedback during cold loads and keep sorted overview
  rows aligned with their asynchronous source-availability results.
- Render heating and cooling as separate red and blue traces in the shared
  transition reviewer, and correctly reload labelled Košice Current Annealing
  `.dat` files instead of mistaking cycle numbers for current values.
