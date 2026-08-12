- Keep the Builder responsive while transition source paths are checked, avoid
  rebuilding clean Annealing/VSM/TMA overview tabs, and precompute VSM cycle
  splitting in the existing background project loader.
- Preserve visible loading feedback during cold loads and keep sorted overview
  rows aligned with their asynchronous source-availability results.
- Render heating and cooling as separate red and blue traces in the shared
  transition reviewer, and correctly reload labelled Košice Current Annealing
  `.dat` files instead of mistaking cycle numbers for current values.
- Replaced the separate legacy VSM transition window with the same compact PyQtGraph queue and Auto/Manual/Not observed controls used for current annealing and TMA; VSM saves now write a portable sidecar and mirror into Builder project state.
- Kept review cycles tied to measured traces so a partially reviewed first cycle cannot make later cycles disappear or display duplicate data, and Save now advances through cycles/targets before the next run.
- Added accessible green, amber, and red freshness indicators with explanatory tooltips to every Builder tab and to the Annealing/VSM/TMA transition subtabs.
