# Keithley current hold/cool batch

The batch runner reuses Current Program Logger's worker and CSV/metadata schema.
It runs each level independently: hold 2, 3, ... 10 mA until three consecutive
Keithley resistance readings reach 180 ohm, or until 600 s. Either outcome
advances to 0.1 mA cooling for 30 s, then turns output off and verifies zero
source level before the next trial. The existing 1 mA trial is not repeated.
The whole batch can take up to 94.5 min plus instrument/startup overhead.

Preview first (default; no output commands):

```powershell
uv run python scripts/current_program_batch.py --resource 'USB0::0x05E6::0x2636::4093243::INSTR' --output-dir 'G:\My Drive\1 Projects\Praha\data\vacuum' --run-name 'Ni50Fe25Ga25_2-3_2_2e-5hPa_6_575g'
```

Add `--live` for hardware execution. Current is bounded to 10 mA, voltage to
3 V. Channel A is local sense, matching the recent 1 mA trial. Each run saves
measurement.csv and metadata.json under a new named directory in the chosen
output folder. A batch manifest is saved under ignored
`artifacts/current-program-batches/`. The controller refuses takeover if the
channel is on, requires an exclusive VISA lock, and refuses concurrent logger
or launcher processes. Faults or missing completion confirmation stop the
batch before the next trial.

Short and contact-loss detection are enabled with 10 ohm short threshold,
25% current-collapse threshold, 1 mA activation floor and 0.25 s confirmation.
They are software protections and do not replace a physical interlock.
The 600 s timeout always proceeds to cooling in this opt-in batch mode; the
normal interactive logger retains its original timeout behavior.

If interrupted, inspect the current trial's metadata, wire and output state
before deciding whether to rerun or skip a level. The runner does not silently
resume or overwrite data.
