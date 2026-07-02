# TMA Hardware Profile

This document is the canonical hardware reference for the current TMA bench. Keep product links, measured limits, and control implications here so future TMA software work starts from the same physical assumptions.

## Hardware Stack

| Role | Hardware | Source | Key facts |
| --- | --- | --- | --- |
| Balance / load feedback | G&G E150Y-C / E150Y-3 laboratory balance | https://www.tronix.cz/sk/p/laboratorni-vaha-g-g-e150y-3-150g-x-0-005g and https://www.gandg.de/download/anleitungen/englisch/EY2015_english.pdf | 150 g range, 0.005 g readability, RS232, zero-load reference is handled in software. |
| Balance / load feedback, Kosice bench | KERN TEWJ 600-2M/B precision balance | https://www.kern-sohn.com/shop/en/products/laboratory-balances/precision-balances/tewj-600-2m-b/ | 600 g range, 0.01 g readability, KERN KCP serial/USB protocol, verified with `SI` requests at 256000 baud and 50 ms poll interval. |
| Linear actuator | StepperOnline 8C15S0504AC5-038RS NEMA 8 captive Acme linear stepper | https://www.omc-stepperonline.com/nema-8-captive-acme-linear-stepper-motor-0-5a-38-2mm-stack-screw-lead-2mm-0-07874-travel-38-1mm-8c15s0504ac5-038rs | 2 mm lead, 0.01 mm full-step travel, 38.1 mm stroke, 0.5 A/phase. |
| Stepper controller | Pololu Tic T500 USB Multi-Interface Stepper Motor Controller, item 3134 | https://www.pololu.com/product/3134 | 4.5-35 V, about 1.5 A/phase without extra cooling, full to 1/8 microstepping, open-loop position/speed control. |

## Bench Provisioning Defaults

TMA includes a bench-provisioning action for copying the setup to a second bench. The operator still has to connect the hardware correctly and choose/confirm ambiguous ports or channels, but the app should configure the normal Košice-style defaults from there:

- HMP4040 current-sweep channel on the current bench: `CH4`.
- HMP4040 motor-supply channel on the current bench: `CH3`, `12 V`, `0.5 A` rail-current limit.
- HMP4040 serial link on the current bench: `COM3` at `115200` baud.
- HMP current-sweep voltage limit: `32.05 V`, matching the observed maximum rather than the older rounded `30 V` value.
- Tic motor current limit: default `343 mA`, matching the bench setting that has enough torque for current experiments while keeping motor heating lower. Treat `500 mA/phase` as the motor-rating ceiling, not the deployment default.
- Tic step mode: `1/8 step`, with `100 full steps/mm` and `800 Tic units/mm`.

Keep the two current limits separate in UI, docs, and troubleshooting. The HMP motor-supply current limit protects the 12 V supply rail feeding the Tic; the current bench mostly ran at `0.4 A`, but one long sweep showed Tic VIN sag while CH2 was configured that way, so the copied-bench default is `0.5 A`. The Tic current limit controls the motor winding current and is the value that most directly affects motor heating and torque.

## Balance Details

### Prague G&G Balance

Known specifications:

- Model family: G&G EY precision balances; the bench unit is the 150 g, 0.005 g readability variant.
- Capacity: 150 g.
- Readability `d`: 0.005 g.
- Linearity: +/- 1 d, about +/- 0.005 g.
- Reproducibility: 2 d, about 0.010 g.
- Minimum weight: 10 d, about 0.050 g.
- Stabilization: the vendor page lists `< 2 s`; the EY manual lists `< 4 s` for this family.
- Warm-up: 30 min before precise use.
- Interface: RS232, 8-bit ASCII, 1 start bit, 8 data bits, 1 stop bit, no parity.
- Supported baud rates: 600, 1200, 2400, 4800, and 9600 bit/s.
- Remote command prefix default: `0x1B` (`ESC`), with `ESC p` for print/read and `ESC t` for tare.
- C1 sensitivity and C2 filtering both use lower values for faster, more sensitive response. `0` is the dispensing-style setting for each.
- The manual does not publish a maximum measuring/update frequency or maximum `ESC p` request rate.
- Current bench link: the balance was verified on `COM6` at `9600` bit/s using the `ESC+p` request. Passive streaming was not observed in this mode.
- Measured request/response cadence on 2026-04-29: 60 samples in a 12 s benchmark, 4.94 Hz achieved rate, mean/median period about 202 ms/sample, 0 timeouts, and a stable raw line of `21.125 g`.
- TMA default for this request-mode balance: 250 ms scale acquisition interval with a 300 ms serial read timeout.

Important control implication:

- The manual explicitly warns against dynamic weighing because internal stability compensation can distort results while load is changing. TMA should therefore treat the balance as a high-resolution, low-bandwidth force signal rather than a fast load cell.
- Fresh force feedback from the current request/response balance is only about 5 Hz. The motor/control loop can run faster, but load/stress decisions must not assume 20 Hz balance data.
- Raising the serial baud rate alone is unlikely to improve the measured 202 ms response, because the transmitted payload is small compared with the balance's internal response time.
- Faster force feedback would require a supported scale-side fast/streaming mode, lower filtering/stability averaging, or a different load sensor.
- Keep the physical balance display in real grams. TMA should continue using the zero-load scale reference to calculate applied wire load.
- Log raw balance readings alongside applied load so dynamic behavior can be audited after each run.

### Kosice KERN KCP Balance

Known and measured settings for the KERN TEWJ 600-2M/B bench balance:

- Capacity: 600 g.
- Readability `d`: 0.01 g.
- Verified TMA profile: USB serial on Windows, `SI` request, CRLF line ending, `256000` baud, and `50 ms` poll interval.
- The same KERN KCP profile also probes `S` requests and lower KERN-supported baud rates for auto-detect fallback, but the preferred bench setting is `256000` baud.
- The scale can provide much faster request/reply cadence than the Prague G&G balance. On the 2026-07-02 Kosice run, `scale_raw.csv` showed median reply spacing near `50 ms`, p95 near `101 ms`, and many repeated adjacent display values.
- The 0.01 g readability is a meaningful control floor. For the mounted 18.2 um wire on 2026-07-02, one display count was about `0.377 MPa`.
- TMA therefore treats KERN feedback as fast but quantized: the control loop can react sooner than with the Prague balance, but it must not classify a single display count as a confirmed worsened response.
- KERN KCP fast-feedback runs use a smaller current-hold command cap than the Prague/G&G profile: `0.08%` base correction strain and `0.092%` adaptive ceiling. The older Prague/G&G caps remain `0.24%` and `0.35%`.
- KERN current-hold resume is response-earned: the app can relax resume only as a fraction of the actual hold-entry error, and only after filtered feedback improves without drifting away from the target. Do not replace this with fixed MPa pause/resume values for one sample.
- Raw scale sidecar logging remains important. Use `scale_raw.csv` to distinguish real load changes from repeated display-count values.

## Linear Actuator Details

Known specifications from the StepperOnline page:

- Manufacturer part number: 8C15S0504AC5-038RS.
- Motor type: captive linear stepper.
- Frame size: NEMA 08, 20 mm x 20 mm.
- Body length: 38.2 mm.
- Stroke length: 38.1 mm.
- Step angle: 1.8 deg, so 200 full steps/rev.
- Lead travel: 2 mm/rev.
- Full-step travel: 0.01 mm/step.
- Rated current: 0.5 A/phase.
- Phase resistance: 12 ohm.
- Inductance: 4.5 mH +/- 20%.
- Holding torque: 0.02 N m.
- Lead screw diameter: 3.5 mm.
- IP rating: IP40.
- Leads: 4-wire bipolar, 300 mm lead length.

Derived motion units:

| Tic microstep mode | Command units per mm | Nominal command travel |
| --- | ---: | ---: |
| Full step | 100 units/mm | 0.010000 mm |
| 1/2 step | 200 units/mm | 0.005000 mm |
| 1/4 step | 400 units/mm | 0.002500 mm |
| 1/8 step | 800 units/mm | 0.001250 mm |

Microstepping improves command granularity and smoothness, but it is not the same as absolute mechanical accuracy. Real positioning is limited by backlash, friction, compliance, motor torque margin, and the lack of a stage position encoder.

## Backlash Status

The actual TMA backlash is not known from the product datasheet. It must be measured on the assembled rig.

StepperOnline's page does not give a numeric backlash value for this captive actuator. It only states generally that standard lead screw and nut assemblies have nominal backlash that can increase after many cycles, and it describes anti-backlash nuts as a separate/custom option for other linear motor styles. That note is not a measured value for the installed TMA actuator.

Treat backlash as an empirical bench parameter:

- `measured_backlash_mm`: unknown / TBD.
- `measured_reversal_load_delay_g`: unknown / TBD.
- `measured_reversal_settle_s`: unknown / TBD.

Recommended characterization:

1. Put the rig under a stable small tensile load.
2. Command a series of same-direction micro-moves and record applied load change per move.
3. Reverse direction with the same move size and count how much commanded travel occurs before the load responds consistently.
4. Repeat at several preload levels because backlash and stiction can be load-dependent.
5. Store the measured take-up distance as the TMA backlash setting, but keep the raw data because the best value may differ between seeking and servo holding.

Until measured, do not assume the actuator has zero backlash. For software defaults, use `0 mm` only as "unknown/not compensated", not as a physical claim.

TMA includes a generic `Calibration` recipe for this measurement. A 0.12 mm copper wire is stable and useful for a first mechanical check, but it is much stiffer than the microwires and may need several grams of preload before it is straight. A mounted microwire can also be used when matching the real experiment geometry matters more than isolating material transformations. The routine is automatic after physical setup: it runs the preload/return length setup when setup is enabled, records still-load noise, uses a separate preload seek to straighten or tension the wire, performs smaller forward/reverse micro-move sweeps, and stores the resulting stiffness/backlash and stress-strain report in the session JSON metadata.

The hanging-weight zero-load scale reference is also the physical applied-load ceiling for this rig: once the balance reading reaches about `0 g`, the weight is airborne and the wire cannot receive more load from that mass. TMA therefore uses the zero-load reference as the default max applied load; the custom lower limit is only for stopping below the installed weight.

## Stress And Load Conversions

For a round wire with diameter `d_um`, the tensile stress per gram of applied load is:

```text
stress_mpa_per_g = 39226.6 / (pi * d_um^2)
target_load_g = target_stress_mpa / stress_mpa_per_g
```

Examples:

| Wire diameter | 1 g load | 0.005 g scale digit | 0.010 g reproducibility | 0.100 g load error |
| ---: | ---: | ---: | ---: | ---: |
| 10 um | 124.9 MPa | 0.62 MPa | 1.25 MPa | 12.49 MPa |
| 13 um | 73.9 MPa | 0.37 MPa | 0.74 MPa | 7.39 MPa |
| 20 um | 31.2 MPa | 0.16 MPa | 0.31 MPa | 3.12 MPa |

For a 13 um wire, a 200 MPa iso-stress target is only about 2.7 g of applied load. This is well inside the balance range, but the control problem is sensitive: a 0.1 g load error is about 7.4 MPa.

## Software Control Implications

The closest practical DMA-like behavior with this hardware is quasi-static iso-stress control with explicit quality metrics.

Recommended software direction:

1. Use the fastest honest balance acquisition for the installed scale and preserve raw scale sidecar data. For the current G&G request/response link, that means planning around about 5 Hz.
2. Keep a slower main session log with load/stress summary statistics.
3. Use target seeking to move between stress levels.
4. Use a continuous servo-hold controller during current sweeps.
5. Estimate live stiffness in `g/mm` from recent motor moves and load response.
6. Convert stress targets to load targets internally, then control applied load.
7. Log target stress, actual stress, stress error, RMS/max error, scale noise, controller output, estimated stiffness, and saturation state.

Initial servo-hold control law:

```text
error_g = target_load_g - filtered_applied_load_g
estimated_correction_mm = error_g / stiffness_g_per_mm
velocity_mm_s = estimated_correction_mm / response_time_s
```

TMA now uses this as a first-pass proportional seeking law: calibration supplies the initial stiffness/noise/backlash prior, stiffness is rescaled by the ratio between the calibrated length and the current unloaded gauge length, live load response can refine the estimate during a run, and commanded correction is clamped by motor resolution, user speed/step ceilings, the real scale-feedback interval, and safety limits. Backlash take-up is kept out of specimen displacement/strain, and small reversals can be skipped when the backlash cost is larger than the predicted target improvement. During setup preload, early slack take-up can use the setup slack `%/s` speed over each fresh scale interval instead of being restricted by the fine preload correction step.

Add integral correction and current-ramp feedforward only after the basic proportional controller is characterized on copper wire, a dummy spring, or several representative microwire lengths.

Current sweeps own their current program. If the supply reaches the configured voltage limit before the requested current, TMA ramps current back down to that sweep's start current at the recipe ramp rate and continues with the next recipe step instead of stopping the experiment.

## Characterization Checklist

Before claiming DMA-like precision, measure and save:

- static scale noise at 0, 1, 2, 5, 10, and 20 g
- achieved scale sample rate and request/response latency; current `COM6`/`9600`/`ESC+p` result is about 4.94 Hz with about 202 ms period
- balance response while C1=0 and C2=0, if the scale menu allows those settings on the bench unit
- load change per full step and per configured microstep
- load-path stiffness in g/mm at representative stress levels
- backlash on direction reversal
- motor command latency through the current control path
- current-supply setpoint latency and actual current quantization
- load disturbance during current sweeps on copper and then microwires

The realistic control bandwidth should be set from these measured values, not from the nominal motor step rate.
