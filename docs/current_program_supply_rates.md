# Current Program Logger: supply profiles and rate evidence

Checked 2026-09-14. A requested polling rate, command-processing time, fresh ADC measurement rate and output settling time are different quantities. None should be substituted for another. These profiles are application limits, not claimed instrument maxima.

## Evidence

- **HMP4030/HMP4040:** the [R&S HMP datasheet, page 10](https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/dl_common_library/dl_brochures_and_datasheets/pdf_1/HMP_dat_en_5215-4981-32_v0201.pdf) lists <50 ms nominal command processing. The [HMP user manual, section 8.5](https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/pdm/cl_manuals/user_manual/1178_6833_01/HMPSeries_UserManual_en_04.pdf) describes about 100 ms for combined APPLy voltage/current setting. Neither establishes fresh paired I/V throughput. The repository driver serializes channel selection plus CURR for a set, and channel selection plus two MEAS queries for an I/V read; each command has a 30 ms software wait, before any additional reply/transport/broker delays. Therefore a set has at least 60 ms of programmed waits and an I/V read at least 90 ms. Concurrent broker users add contention. No HMP device was accessed for this change. Operator experience suggests about 2 Hz readback; this is explicitly provisional, not a verified hardware maximum.
- **Keithley 2636B:** [manufacturer specifications](https://www.tek.com/de/documents/specification/models-2634b-2635b-and-2636b-system-sourcemeter-instrument-specifications) give speed tables dependent on NPLC, trigger, script/sweep mode and interface. At 0.01 NPLC, internally triggered measure-to-memory using scripts reaches 4,000/s at 50 Hz mains; that is NOT a USB host-loop or simultaneous I/V guarantee. The existing adapter configures 0.01 NPLC, disables autozero/filter/delay and requests measure.iv(). Existing 500 Hz control / 1,000 Hz polling application ceilings are retained; this change does not claim they are always achieved. No Keithley was benchmarked here.
- **Siglent SPD1305X:** the [programming manual](https://www.siglentna.com/wp-content/uploads/dlm_uploads/2018/05/SPD1000X_UserManual_UM0501X-E02A.pdf), section 3.4, documents separate current and voltage queries. No guaranteed fresh ADC update rate was found in that manual or the [service specifications](https://siglentna.com/wp-content/uploads/dlm_uploads/2020/07/SPD1000X-service-manual.pdf). The sub-50-microsecond load transient response specification is not remote-command latency. A direct benchmark was therefore performed, as below.

## Siglent USB benchmark

Device SPD13ECQ801071, firmware 2.1.2.11, hardware V2.0; exclusive VISA ownership. Output and timer were OFF throughout; voltage setpoint zero; current settings alternated 1 and 5 mA. Each phase had 100 serial repetitions. No reset, firmware change or energization. Final output OFF and voltage/current setpoints zero verified; error queue `+0, No error`.

| Operation | Median ms | p95 ms | Maximum ms |
| --- | ---: | ---: | ---: |
| Set current + query/verify setpoint | 4.893 | 32.182 | 32.468 |
| Separate current + voltage queries | 4.420 | 5.566 | 34.403 |
| Set current + I/V read + query/verify setpoint | 37.180 | 42.978 | 68.140 |

Raw timings and reproducible test script are under ignored `artifacts/siglent-off-timing-result.json` and `artifacts/siglent_off_timing.py`. Timing includes completed responses, not just writes accepted by the OS. The combined test includes an extra setpoint query compared with the normal worker. Output-off measurements cannot prove ADC freshness, output settling, loaded accuracy or worst-case latency. Earlier short no-load tests returned roughly 10 ms I/V pairs, showing why one fastest number is insufficient.

## Application policies

| Profile | Default control / read / protection Hz | Control ceiling Hz | Read/protection ceiling Hz |
| --- | --- | ---: | ---: |
| Simulation | 100 / 10 / 100 | 500 | 1000 |
| Keithley 2636B | 100 / 10 / 100 | 500 | 1000 |
| HMP broker | 2 / 2 / 2 | 5 | 2 |
| Siglent SPD1305X | 10 / 10 / 10 | 10 | 20 |

HMP's 5 Hz set ceiling leaves room below the driver's 60 ms/set software-wait bound; simultaneous read traffic and instrument delays can still reduce delivered rates. The 2 Hz read ceiling follows the operator's reported experience pending measurement on that path. Siglent's 100 ms minimum requested control interval exceeds the worst 68.1 ms combined operation observed in this small benchmark; the 50 ms read interval exceeds the 34.4 ms read-only maximum. These margins are policies, not guaranteed deadlines; combinations may miss requested intervals. Do not use them as safety-interlock guarantees. UI and CSV rates cannot force polling beyond the profile ceiling. Resistance-conditioned recipe steps remain disabled for HMP/Siglent because their <=5 ms confirmation gaps are not qualified.

## Profile persistence and startup

Connection settings, electrical limits, rates, logging preferences and recipes are saved per supply type. Output directory, run name and mechanical load stay global. Legacy flat settings are imported only into the last selected supply, clamped to its application ceilings, and left intact for rollback. The UI warns when clamping occurs. Profiles are not a specimen-specific safety approval.

Selecting a VISA supply automatically enumerates resources without opening/energizing instruments. Only matching USB vendors are offered; multiple matches require a choice. Saved LAN addresses can be entered manually. Late results cannot replace a newer selection or an operator edit. Discovery has a five-second UI timeout, bounded outstanding jobs, and no output commands.

Siglent startup remains at the explicitly configured initial current, with no recipe advancement, while requesting two usable baselines approximately 100 ms apart within a one-second qualification window (plus bounded instrument I/O latency). Finite zero readings are retried; malformed readings and enabled electrical fault detectors are not ignored. The upper resistance limit still acts on the first applicable sample. Persistent invalid/zero readings shut down with actual I/V values and startup samples in metadata. One second is a bounded application qualification window, not a manufacturer settling-time guarantee. This software behavior has synthetic coverage but has not been validated on the mounted wire.
