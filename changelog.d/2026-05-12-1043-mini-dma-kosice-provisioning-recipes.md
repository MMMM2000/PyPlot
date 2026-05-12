2026-05-12 10:43

- Mini DMA adds recipe JSON save/load with descriptive generated filenames for current-sweep recipes.
- Mini DMA adds bench provisioning for copied setups, including HMP motor-supply setup, Tic current-limit application, and pass/fail hardware status reporting.
- Mini DMA updates the HMP4030 current-sweep voltage limit to 32.05 V and defaults the copied-bench motor supply to CH2 at 12 V / 0.5 A while keeping current annealing on CH3.
- Mini DMA keeps the copied-bench Tic motor current limit at the cooler bench-proven 343 mA default and makes emergency stop disable the motor-supply channel as well as the current-sweep output.
