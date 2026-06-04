2026-05-15 09:27

- Changed AC susceptibility acquisition from a fixed number of LCR readings per point to a fixed measurement time per point, defaulting to 10 seconds.
- Updated baseline and microwire sweep estimates/progress to use elapsed measurement time so frequency-dependent LCR response rates do not bias stability checks.
- Documented that the logger records every successful `FETC:IMP?` reply during the time window, not necessarily every internal LCR conversion.
