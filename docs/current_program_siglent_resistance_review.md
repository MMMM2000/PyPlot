# Siglent resistance artifacts: completed runs on 2026-09-14

Read-only review of the user's runs ending `155835` and `160031`; no hardware activity and no changes to recorded data.

## Evidence

- Both use Siglent SPD1305X, 10 Hz control, 20 Hz I/V polling, 10 Hz live UI, but only **1 Hz CSV**. Consequently the CSV cannot resolve all the fine teeth or brief spikes visible in the live plot. There are 34 and 133 CSV rows respectively.
- All recorded current readings are whole milliamps. The 1 mA setpoint/readback resolution is consistent with the [manufacturer's specifications](https://siglentna.com/wp-content/uploads/dlm_uploads/2020/07/SPD1000X-service-manual.pdf), which also list current accuracy ±(0.3% of reading + 10 mA). Resolution is not accuracy, and the accuracy bound is not a noise amplitude.
- The second run's startup samples show current 0 mA while voltage changes from 30.000 V to 1.562 V at about 0.233 s; current becomes 9 mA at about 0.343 s. This demonstrates staggered changes in reported quantities. It does not prove a physical 30 V transient: independent instrumentation would be needed to distinguish stale output-off telemetry from actual output behavior.
- At 61.825 s, the second run reports target 3.833 mA, measured current 1 mA, voltage 0.417 V, hence R=417 ohm. At 121.582 s, it reports target 3.460 mA, measured current 1 mA, voltage 0.351 V, hence R=351 ohm. These spikes repeat near the low-current turnaround of the 60 s recipe cycle, not at arbitrary unrelated times. Zero-current rows have undefined R, correctly stored blank.
- In the first run the **230 ohm legacy threshold really tripped** at approximately 18.544 s; the trigger row reports 32 mA, 7.401 V, R=231.28125 ohm. The configured action then reduced the current to the 10 mA initial/readout value. This was not an unexplained spontaneous reduction. The large subsequent live spike is not preserved in the sparse CSV; mismatched I/V ages during the drop are a plausible explanation, not a proven reconstruction.
- The first screenshot shows contact-loss confirmation at 10 s although that run's metadata records 0.2 s. The later run records 10 s. Both runs had the new short/contact-loss checks **disabled**, so that setting did not cause either run's behavior. This discrepancy supports the need to prevent inadvertent UI edits, but cannot establish how the value was changed.

## Interpretation

Fine repeating sawtooth patterns are strongly consistent with quantized current and independently updating I/V values during ramps. R=V/I amplifies denominator steps: one 1 mA step is 20% of 5 mA, 10% of 10 mA, and 2% of 50 mA. The first upward ramp crosses a 1 mA step every 0.75 s (40 mA/30 s); subsequent upward ramps cross one about every 0.612 s (49 mA/30 s). A 1 Hz CSV aliases these features. This does not establish the instrument's fresh ADC rate or exclude real contact/heating effects.

The broader cycle-dependent resistance curve may include genuine thermal/transformation behavior. Do not remove it or all spikes with an unqualified smoothing filter, and do not use commanded current instead of measured current to make the curve look smoother. No measurement/safety logic was changed in this review.

For a subsequent authorized characterization, use constant-current holds within an approved operating envelope and log all requested readbacks (currently up to 20 Hz) to distinguish step-correlated artifacts from periodic variation at steady current. This still does not create fresh ADC data faster than the instrument supplies it. For quantitative low-current resistance, use an independently suitable simultaneous I/V measurement or the Keithley under validated settings. Keep raw measurements and safety decisions separate from any future display-only filtering.
