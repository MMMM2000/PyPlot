2026-05-26 15:41

- Added a Microwire Data Builder automation database-folder mode that promotes a generated project to `microwire_database_latest.pydpj`, writes `update_manifest_latest.json`, and archives the previous latest files with a timestamp.
- Added `exclude_dir_names` for Builder section update recipes so archived or diagnostic run folders can be skipped during recursive measurement imports.
- Added current annealing support to Builder `update_section` automation recipes.
