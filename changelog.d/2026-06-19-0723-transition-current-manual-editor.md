2026-06-19 07:23

- Added shared manual transition-current editing controls for Current annealing review and Current density previews.
- Saved manual As1/Af1/Ms1/Mf1/As2/Af2/Ms2/Mf2 overrides through the existing annealing phase-points payload so Assemble/export values stay consistent.
- Fixed Current density sample keys so samples without a suffix are not stored as a literal `None` suffix.
- Fixed Windows offscreen Qt screenshots by pointing the offscreen platform at readable system fonts.
