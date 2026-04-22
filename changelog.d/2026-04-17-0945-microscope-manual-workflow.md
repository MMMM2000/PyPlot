2026-04-17 09:45 UTC

- Fixed the microscope refresh flow so saved manual `d`/`D` values persist instead of disappearing after a refresh.
- Changed microscope refresh to merge in newly discovered rows and image references without rebuilding or backfilling existing manual values.
- Removed OCR-driven microscope and video extraction paths; those workflows are now manual review and entry only.
- Removed the PaddleOCR/PaddleX/PaddlePaddle dependency chain from the project requirements.
