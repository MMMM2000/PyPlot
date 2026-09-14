### Added

- Add an experimental Current Program Logger with editable ramp, pulse, and hold blocks, including indefinite holds, finite or endless cycle repetition, continuous CSV logging, recipe import/export, simulation, shared-HMP-broker operation, and VISA control of Keithley 2636/2636B SMUs.
- Separate current control, measurement/logging, and UI refresh rates, defaulting to 100 Hz, 10 Hz, and 10 Hz respectively, and use fixed-range combined I/V acquisition for responsive Keithley operation.
- Default new runs to a conservative 1 V compliance setting while keeping the voltage limit operator-adjustable.
- Warm up the first Keithley acquisition before recipe timing, plot resistance on the live time chart's right axis, and add a resistance-versus-current view with electrical power on its top axis.
- Match Current Annealing's power-axis calculation, hide live-plot legends, and restore the complete connection, rate, output, execution, and ordered block setup across app restarts.
- Add a saved-run measurement-history browser and a persistent maximum-resistance latch that allows decreases but prevents any later current increase above the level applied when the threshold was reached.
- Autorange the Keithley voltage measurement so low-voltage resistance readings use an appropriate measurement range instead of inheriting the much larger compliance-voltage range.
