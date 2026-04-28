# Mini DMA Hardware Profile

This document is the canonical hardware reference for the current Mini DMA bench. Keep product links, measured limits, and control implications here so future Mini DMA software work starts from the same physical assumptions.

## Hardware Stack

| Role | Hardware | Source | Key facts |
| --- | --- | --- | --- |
| Balance / load feedback | G&G E150Y-C / E150Y-3 laboratory balance | https://www.tronix.cz/sk/p/laboratorni-vaha-g-g-e150y-3-150g-x-0-005g and https://www.gandg.de/download/anleitungen/englisch/EY2015_english.pdf | 150 g range, 0.005 g readability, RS232, zero-load reference is handled in software. |
| Linear actuator | StepperOnline 8C15S0504AC5-038RS NEMA 8 captive Acme linear stepper | https://www.omc-stepperonline.com/nema-8-captive-acme-linear-stepper-motor-0-5a-38-2mm-stack-screw-lead-2mm-0-07874-travel-38-1mm-8c15s0504ac5-038rs | 2 mm lead, 0.01 mm full-step travel, 38.1 mm stroke, 0.5 A/phase. |
| Stepper controller | Pololu Tic T500 USB Multi-Interface Stepper Motor Controller, item 3134 | https://www.pololu.com/product/3134 | 4.5-35 V, about 1.5 A/phase without extra cooling, full to 1/8 microstepping, open-loop position/speed control. |

## Balance Details

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

Important control implication:

- The manual explicitly warns against dynamic weighing because internal stability compensation can distort results while load is changing. Mini DMA should therefore treat the balance as a high-resolution, low-bandwidth force signal rather than a fast load cell.
- Keep the physical balance display in real grams. Mini DMA should continue using the zero-load scale reference to calculate applied wire load.
- Log raw balance readings alongside applied load so dynamic behavior can be audited after each run.

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

The actual Mini DMA backlash is not known from the product datasheet. It must be measured on the assembled rig.

StepperOnline's page does not give a numeric backlash value for this captive actuator. It only states generally that standard lead screw and nut assemblies have nominal backlash that can increase after many cycles, and it describes anti-backlash nuts as a separate/custom option for other linear motor styles. That note is not a measured value for the installed Mini DMA actuator.

Treat backlash as an empirical bench parameter:

- `measured_backlash_mm`: unknown / TBD.
- `measured_reversal_load_delay_g`: unknown / TBD.
- `measured_reversal_settle_s`: unknown / TBD.

Recommended characterization:

1. Put the rig under a stable small tensile load.
2. Command a series of same-direction micro-moves and record applied load change per move.
3. Reverse direction with the same move size and count how much commanded travel occurs before the load responds consistently.
4. Repeat at several preload levels because backlash and stiction can be load-dependent.
5. Store the measured take-up distance as the Mini DMA backlash setting, but keep the raw data because the best value may differ between seeking and servo holding.

Until measured, do not assume the actuator has zero backlash. For software defaults, use `0 mm` only as "unknown/not compensated", not as a physical claim.

Mini DMA includes a generic `Calibration` recipe for this measurement. A 0.12 mm copper wire is stable and useful for a first mechanical check, but it is much stiffer than the microwires and may need several grams of preload before it is straight. A mounted microwire can also be used when matching the real experiment geometry matters more than isolating material transformations. The routine is automatic after physical setup: it can run the zero-load/length setup, records still-load noise, uses a separate preload seek to straighten or tension the wire, performs smaller forward/reverse micro-move sweeps, and stores the resulting stiffness/backlash and stress-strain report in the session JSON metadata.

The hanging-weight zero-load scale reference is also the physical applied-load ceiling for this rig: once the balance reading reaches about `0 g`, the weight is airborne and the wire cannot receive more load from that mass. Mini DMA therefore uses the zero-load reference as the default max applied load; the custom lower limit is only for stopping below the installed weight.

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

1. Use fast balance acquisition and preserve raw scale sidecar data.
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

Clamp the commanded velocity and target travel by safe limits. Add integral correction and current-ramp feedforward only after the basic proportional controller is characterized on copper wire or a dummy spring.

Current sweeps own their current program. If the supply reaches the configured voltage limit before the requested current, Mini DMA ramps current back down to that sweep's start current at the recipe ramp rate and continues with the next recipe step instead of stopping the experiment.

## Characterization Checklist

Before claiming DMA-like precision, measure and save:

- static scale noise at 0, 1, 2, 5, 10, and 20 g
- achieved scale sample rate and request/response latency
- balance response while C1=0 and C2=0
- load change per full step and per configured microstep
- load-path stiffness in g/mm at representative stress levels
- backlash on direction reversal
- motor command latency through the current control path
- current-supply setpoint latency and actual current quantization
- load disturbance during current sweeps on copper and then microwires

The realistic control bandwidth should be set from these measured values, not from the nominal motor step rate.
