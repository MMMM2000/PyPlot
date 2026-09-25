# XRD wire heating: software protections and remaining safety design

## Implemented software layer (2026-09-14)

See [supply profiles and rate evidence](current_program_supply_rates.md) for per-device settings, automatic VISA discovery, the bounded Siglent startup baseline phase, manufacturer references, and the output-off timing benchmark. HMP read/protection polling is provisionally capped at 2 Hz, not 20 Hz; Siglent's limits distinguish setting from readback polling and do not claim fresh ADC rates.

The setup panel now has independent, opt-in short-circuit and broken-wire/contact-loss checks. Configure specimen-specific thresholds; the displayed defaults are examples, not a validated operating envelope. These checks take precedence over the legacy maximum-resistance hold/readout action:

- Short: at target current >= the configured activation floor and measured current >= that floor, trip on one measured resistance sample <= the configured minimum.
- Broken wire/contact loss: at target current >= the activation floor, trip when measured current stays below the configured fraction of target for the confirmation interval. Insufficient voltage compliance can produce the same signature and also trips. A healthy sample or an intentional below-floor/zero-current step resets confirmation.
- Invalid I/V while electrical monitoring is enabled also faults. No short/open detection is promised below the activation floor. The Siglent's 1 mA quantization and approximately 10 mA accuracy offset must be considered when choosing thresholds; a 5 mA setpoint is not a guaranteed 5 mA actual-current ceiling.
- A trip attempts output shutdown before fault logging/UI delivery, ends the worker, and blocks further starts until operator acknowledgement. Acknowledgement does not energize anything; inspect connections and verify output OFF first. The in-memory latch is not a persistent hardware interlock. Closing/reopening the application never automatically starts a run.

Uncheck **Record measurements to CSV** for display-only measurements. Settings and final fault/summary metadata still require an output directory and are saved. CSV defaults to 1 Hz independently of acquisition, protection and UI rates. Acquisition uses the highest requested applicable rate; actual instrument latency may limit it. Logging has a 2,048-row bounded queue, a separate disk thread and roughly one-second flush intervals (immediate requested flush for fault rows). Disk errors or queue saturation stop the run; queued data may be incomplete on storage failure. Shutdown happens before a bounded two-second writer join, so a blocked writer cannot indefinitely delay the shutdown attempt. Metadata writes happen before activation and after shutdown, not in the energized acquisition loop.

Live history retains at most the selected 100–20,000 points (default 10,000), including data used by plots and bundle estimates. Old live points are discarded independently of CSV retention. At most one regular sample/state pair is outstanding to the GUI; slow rendering drops live updates rather than accumulating an unbounded Qt event queue. Only the latest 1,024 sequence events are retained in metadata.

The worker acquires the shared Windows sleep guard before opening/configuring the supply and releases it after shutdown, on the same thread. The screen may turn off. Failure to acquire prevents hardware activation. This blocks idle sleep only, not deliberate sleep, shutdown, USB loss or power failure.

These changes have software/fake-device coverage, not a live open/short qualification. Do not create faults on a mounted microwire to test them. Independent hardware cutoff and facility approval are still needed for unattended operation.

## Remaining design and qualification

The SPD1305X adapter adds supply control, not an XRD safety system. Do not equate the supply's short-circuit/overload self-protection with protection of a microwire, contacts, or the XRD instrument. XRD radiation/enclosure interlocks must remain independent and unchanged.

## Proposed layers

1. Establish the XRD facility's permitted electrical connections, grounding, feedthroughs and isolation. Insulate exposed contacts and secure leads against movement. A short to chassis can bypass the intended wire path and may not be recognizable from supply I/V alone.
2. Select conservative physical current and voltage ceilings for the particular specimen. In a current-limited CV/CC supply, an open circuit can charge the output toward the voltage ceiling; reconnection may discharge stored energy before software reacts. A lower ceiling, appropriate series impedance/current limiting, and an independent rated output disconnect should be evaluated with a dummy load first.
3. Implement a separate, latched XRD fault mode: any fault means output OFF, not current hold or nonzero readout. No automatic restart after reconnecting, cooling, software restart or communication recovery; require explicit operator reset and a new low-energy continuity check.
4. Detect both low and high resistance (short/contact bypass versus damaged contact), unexpected low measured current while nonzero current is commanded, approaching voltage compliance without reaching commanded current, excess voltage/current/power/energy, and run-duration limits. Treat zero current as undefined resistance, not zero ohms or a silently discarded sample. Baselines and thresholds must be specimen-specific, with minimum measurable current and allowances for startup, quantization, thermal transition and noise.
5. Add monotonic deadlines for missing/invalid data and stalled acquisition, recording trigger, timestamps and last measurements. The normal UI must not be the only place evaluating faults. Separate software response-time measurements from guarantees: process crashes or unplugged USB can prevent an OFF command from arriving.
6. For fail-off behavior on PC/USB failure, use a verified independent watchdog/interlock and appropriately rated normally-open disconnect. A finite onboard sequence is not automatically a watchdog: its ending/repeat behavior must be documented and tested. No communication-watchdog capability has been verified for the SPD1305X.

## SPD1305X limitations

The present adapter forces local 2-wire sensing. The 4-wire checkbox belongs to the Keithley and is hidden in Siglent mode. Local voltage includes leads/contacts; remote sense compensation is not a direct resistance meter and broken sense leads require separate analysis.

The manufacturer lists 1 mA resolution and current setting/readback accuracy of ±(0.3% of reading + 10 mA). That is not a useful precision guarantee near 10 mA. Use a suitable external current measurement and voltage measurement or the Keithley for quantitative low-current resistance/safety thresholds. Our no-load test verified only commands, output state and telemetry, not this supply's loaded current performance.

The observed pair of separate USB current and voltage queries took about 9.7–10.3 ms. These values are not simultaneous and do not establish ADC freshness or worst-case latency. Existing resistance-step completion requires gaps <=5 ms and therefore is blocked in the Siglent UI. Existing global maximum-resistance actions are unchanged and are not the complete proposed XRD fault mode.

## Before connecting the XRD wire

- Specify expected cold/hot resistance, operating current, voltage ceiling, maximum dwell and acceptable fault energy.
- Verify grounding/isolation and allowed wiring with the XRD owner; do not bypass enclosure or radiation interlocks.
- With an expendable dummy load, test normal heating, open output, controlled short, intermittent contact, stale reads, USB removal and process failure. Measure the actual output transient and fault-to-disconnect time with independent instruments; verify final output state after each test.
- Only then approve a specimen-specific operating envelope. The later mounted-wire request was clarified as a maximum **programmed** current of 5 mA; it does not authorize deliberate open/short testing or higher-current XRD heating. The brief test aborted at the first 1 mA / 0.000 V readback, without reaching 3 or 5 mA; output-off and zero setpoints were confirmed. This is not proof of a short or a qualified resistance measurement.

Sources: [SPD1000X user/programming manual](https://www.siglentna.com/wp-content/uploads/dlm_uploads/2018/05/SPD1000X_UserManual_UM0501X-E02A.pdf), [SPD1000X service manual specifications](https://siglentna.com/wp-content/uploads/dlm_uploads/2020/07/SPD1000X-service-manual.pdf).
