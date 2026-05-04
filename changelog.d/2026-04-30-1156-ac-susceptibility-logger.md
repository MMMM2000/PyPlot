2026-04-30 11:56
- Added an AC Susceptibility Logger that reuses current annealing ramp/hold/reverse behavior while logging GW Instek LCR-6200/LCR-6000 impedance readings.
- Added LCR-6000 protocol helpers, a hardware probe script, and documentation for driver setup plus first-pass frequency/amplitude sweep settings.
- Prevented connected LCR configuration failures from silently starting an annealing run, and covered AC log formatting/header behavior with focused tests.
- Added an LCR-only baseline workflow that records repeated empty-fixture or wire/no-current readings without starting the current annealing power-supply path.
