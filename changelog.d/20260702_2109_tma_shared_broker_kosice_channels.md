2026-07-02 21:09

- Updated TMA shared-HMP broker bench defaults to the current Kosice HMP4030 wiring: CH2 for the Tic motor rail and CH3 for microwire current sweep.
- Updated shared-HMP validation docs so broker checks, no-wire current smokes, motor-power checks, and final readbacks use the current CH1/CH2/CH3 bench layout.
- Documented that TMA can set and auto-detect the KERN PC-side serial preset, while the balance's internal `prMode`, `triG`, `cont`, speed, zero, and stability menu settings remain manual unless a safe KERN remote-write path is verified.
