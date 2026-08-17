Fixed
-----

- Keep the Microwire Data Builder responsive while large measurement payloads are encoded and written during project saves, and avoid reopening every TMA source run while reconciling transition reviews.
- Exclude TMA iso-current and iso-strain measurements from the transition-review queue instead of reporting them as load failures.
- Show TMA strain in ordinary percent values without PyQtGraph's confusing automatic milli scaling.
