# Phone-video pulse / low-power-hold screen

Prepared on 2026-10-01. Preparation does not authorize a live run.
The existing interactive logger and its saved settings are unchanged.

## Protocol

First run and review a no-hold reference pilot. Then run four independent
cold-start trials, with hold currents **3.0, 2.5, 2.0, 3.0 mA**, in that order:

| Phase | Programmed current | End condition |
|---|---:|---|
| Cold reference | 0.1 mA | 3 s, stable/in-band resistance required before heating |
| Heating | 10 mA | 0.5 s target duration, or three consecutive R >= 185 ohm readings |
| Low-power hold | trial current | 10 s; omitted in the reference pilot |
| Cooling | 0.1 mA | at least 20 s AND qualified cold reset |

A **190 ohm guard** overrides heating or holding directly to a fresh cooling
phase. It never advances into a potentially still-hot hold. That hold is
marked interrupted. The additional **195 ohm emergency cutoff** turns output
OFF and stops the batch. Reconfirm these specimen-specific thresholds before
live execution; these are software protections, not proof against damage.

Ceilings: 10 mA programmed current and 3 V compliance, Keithley channel A,
local two-wire sense. Short threshold: 10 ohm, open/contact-loss threshold:
25% of target sustained for 0.25 s, both active at targets >= 1 mA. The
0.1 mA cooling phase uses valid-resistance/reset checks instead of those
high-current fault thresholds. Invalid readings, disk backlog/failure,
ownership conflicts, cancellation, reset timeout or failed OFF verification
stop the screen; there is no automatic retry.

Cold reset compares two consecutive half-window resistance medians over a
2-second window against the FIRST trial's cold reference, with a provisional
fixed +/-5 ohm band. It also checks median stability. Individual noisy readings
are retained, not smoothed out of the CSV. Review the band against pilot noise;
do not widen it just to admit a warm tail. Electrical recovery is only a proxy
for mechanical reset. Cooling may extend to 90 s total, then the batch stops
instead of proceeding warm. A normal four-trial screen takes about 134 s plus
startup/USB overhead; budget 3 minutes plus the separate pilot and its review.

## What is saved

The usual measurement.csv/metadata.json schema is retained. Acquisition and
safety polling target 1000 Hz throughout. CSV saving targets 1000 Hz during
the baseline, pulse, hold and first 2 s of cooling, then 100 Hz during the
remaining cooling. These rates are requests, NOT claimed achieved rates.
Keep every saved raw I/V pair, derived raw resistance and power. Check achieved
spacing, gaps and protection statistics before analysing fast transients.

Metadata adds the recipe's actual monotonic clock origin, UTC origin,
current-command issue/completion times, cold-reference/reset qualification
and output-OFF verification. A unique batch manifest points to each trial,
records guard interruptions and actual pulse command intervals. Duplicate
controllers are refused and the selected channel must be OFF before takeover;
exclusive VISA locking is required. Output OFF and zero setpoint are queried
before the connection is released between trials and at the end.

The pulse is currently HOST-TIMED. USB/OS scheduling and blocking commands can
extend a nominal 0.5 s pulse. The runner rejects further trials if the measured
command interval exceeds 0.55 s; this is a diagnostic stop, NOT a hard hardware
0.5 s guarantee. Review the pilot interval and waveforms before approving the
screen. If unacceptable, use instrument-side triggering/timing before running
the screen. Idle sleep prevention remains active, but explicit sleep, crashes,
power loss and disconnected communications can defeat software protections.

## Video synchronization: no matching phone/PC wall clocks needed

Use ordinary 60 fps video, not timelapse or slow-motion playback. Turn Auto FPS
/ Auto Low Light FPS OFF if available. Lock focus/exposure, use steady bright
lighting, and keep the phone rigid. Put the moving weight, a fixed millimetre
scale and the **PC sync-board window in the same view**, without sacrificing
the ability to resolve motion. Start recording before launching live control.
If the screen cannot be included, stop and choose a separate logged optical
cue; do not substitute first wire movement for the electrical start time.

`--sync-board` shows a large PC monotonic time and a numbered/binary cue changing
every 250 ms. Its CSV records the same clock used by the worker. Keep it visible
before the first pulse and after the final trial. Closing the window requests
cancellation. The board remains open after completion, with the instrument
already released and output OFF verified. Record a few more seconds, then close
it and stop recording. Its log lives under ignored `artifacts/current-program-batches`.
Each board displays its unique sync-session ID to distinguish pilot/screen
cue logs in one continuous video. While the board is open it keeps the PC and
display awake, since the screen is needed as a recorded synchronization cue.

Afterward identify cue numbers/transitions in the ORIGINAL phone video,
match them to the cue CSV, and fit video presentation timestamps to PC
monotonic time. Use cues throughout to check offset/drift. Per-trial elapsed
time is `PC monotonic time - sequence_started_monotonic_s`. Do not assume
frame number / 60 is exact, or trust the file creation time as a sync trigger.
At 60 fps, frames are nominally 16.7 ms apart. Screen paint timestamps are NOT
optical emission timestamps: display refresh/latency, camera exposure and
rolling shutter add uncertainty. Report it; this is not sub-millisecond sync.
No third-party app or timestamp overlay alone fixes unsynchronized clocks.

Original .MOV/MP4 files must be transferred without recompression or trimming.
Frame presentation timestamps can be inspected with FFprobe. The previously
used NBElias task "Plan synchronized video and graph" can handle later
alignment/segmentation once the source video and logs are available.

## Tomorrow's preflight and commands

Reconfirm wire/contacts, 6.575 g load if unchanged, vacuum pressure, channel A,
sense wiring, current/voltage/resistance limits and available storage. Close
both logger windows, other instrument controllers and the launcher. Confirm
the phone is recording and the moving marker/scale and sync board are visible.
Do not copy permissive 100 mA / 20 V settings from old manual measurements.
The resource below is historical: verify connected identity before execution.

From the repository, preview (no VISA access and no measurement files):

```powershell
uv run python scripts/current_program_hold_batch.py --output-dir 'G:\My Drive\1 Projects\Praha\data\vacuum' --run-name 'Ni50Fe25Ga25-hold-screen'
```

Pilot, only after fresh live authorization and phone recording:

```powershell
uv run python scripts/current_program_hold_batch.py --output-dir 'G:\My Drive\1 Projects\Praha\data\vacuum' --run-name 'Ni50Fe25Ga25-reference' --pilot --live --sync-board
```

Review visible contraction, pulse timing/readback, baseline/reset noise and
guard behaviour. Close the pilot's sync board. Do not automatically lengthen
an inadequate pulse. Then, only if the pilot is accepted:

```powershell
uv run python scripts/current_program_hold_batch.py --output-dir 'G:\My Drive\1 Projects\Praha\data\vacuum' --run-name 'Ni50Fe25Ga25-hold-screen' --live --pilot-reviewed --sync-board
```

Default historical VISA resource: `USB0::0x05E6::0x2636::4093243::INSTR`;
use `--resource` to supply the freshly confirmed device. Each run directory
and manifest is new; no existing measurements are overwritten.

## Later analysis

Plot raw resistance/current/power vs time and synchronized displacement.
Compare hold-end shortening with initial shortening, then release time from
the ACTUAL drop to cooling. Integrate MEASURED V*I over pulse, hold and full
cycle to obtain joules; do not integrate resistance or programmed power.
A 10 s hold is a screening result, not evidence of indefinite austenite
retention. Use motion/force (and later XRD) to substantiate retained state.

Sources: [Apple recording options](https://support.apple.com/en-ca/guide/iphone/iphc1827d32f/ios),
[Blackmagic Camera timecode features](https://apps.apple.com/us/app/blackmagic-camera/id6449580241),
[FFprobe frame inspection](https://ffmpeg.org/ffprobe.html).
