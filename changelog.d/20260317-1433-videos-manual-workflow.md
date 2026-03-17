2026-03-17 14:33 UTC
- Fixed Microwire Data Builder video matching so fabrication videos under Google Drive shortcut folders resolve to the correct draw/piece rows and `Open video(s)` works from the Videos table.
- Updated the Videos workflow to be manual-first, including microscope-style red/green completion highlighting for fabrication fields filled in from video review.
- Fixed fabrication row rebuilding so microscope-only wires can inherit matching fabrication data instead of staying as empty placeholders.
- Stopped the Fabrication tab from borrowing `d`, `D`, and `d/D` from microscope rows; those values now come only from fabrication spreadsheets and remain blank otherwise.
- Scoped Videos refreshes to measured wires only and added a dedicated Fabrication missing-data dialog so long missing-wire lists are readable instead of being truncated in the status text.
