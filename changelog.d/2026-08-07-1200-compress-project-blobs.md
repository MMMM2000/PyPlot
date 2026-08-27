Changed
-------
- Compress numerical blob entries when saving Microwire Data Builder projects, reducing .pydpj storage and synchronization overhead without changing their contents.
- Keep the large derived VSM temperature-scan cache in memory for the current session to avoid duplicating it in local JSON storage or weakening safe-codec limits.
