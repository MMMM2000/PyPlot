Fixed
-----

- Keep the Microwire Data Builder responsive while large measurement payloads are encoded and written during project saves, and avoid reopening every TMA source run while reconciling transition reviews.
- Exclude TMA iso-current and iso-strain measurements from the actual transition-review queue instead of reporting them as load failures, without filtering current-annealing runs.
- Show TMA strain in ordinary percent values by resetting PyQtGraph's retained milli scale as well as disabling its confusing automatic SI prefix.
