# AC Susceptibility Logger

The AC Susceptibility Logger combines the existing current annealing ramp with a
GW Instek LCR-6200/LCR-6000 series meter. The first three logfile columns remain
compatible with current annealing plots:

For the full hardware/code handoff, including driver setup, probe results,
verified commands, and next experimental steps, see
`docs/ac_susceptibility_handoff.md`.

```text
Current (mA)    Voltage (V)    Resistance (Ohm)
```

The logger then appends the LCR setting and the latest `FETC:IMP?` response:

```text
AC plan index
LCR frequency (Hz)
LCR level mode
LCR level
LCR function
LCR primary
LCR secondary
LCR monitor1
LCR monitor2
LCR comparator
LCR raw
```

## Hardware Notes

The meter in the lab photo is a GW Instek LCR-6200, part of the LCR-6000 series.
Use the rear USB-B port for remote control. The official manual says the USB
device appears as a CDC virtual COM port after the vendor driver is installed.

Official resources:

- Manual: https://www.gwinstek.com/en-US/products/downloadSeriesDownNew/10211/757
- Product page and USB driver: https://www.gwinstek.com/en-global/products/detail/LCR-6000

On this Windows machine the meter was detected as `USB\VID_2184&PID_005F`, but
Windows reported `CM_PROB_FAILED_INSTALL` until the GW Instek/FTDI VCP driver is
installed with administrator rights. After the driver binds, the meter should
show up under Ports as a normal `COMx` port.

Quick probe after driver installation:

```powershell
.\.venv\Scripts\python.exe scripts\probe_lcr6000.py --list
.\.venv\Scripts\python.exe scripts\probe_lcr6000.py COM7 --configure --fetch
```

## Measurement Plan

The logger builds a frequency-by-level matrix from the LCR panel. With **One
current sweep per AC setting** enabled, it sets the current annealing loop count
to the number of generated AC settings, enables reverse-to-zero, and advances
the LCR setting after each completed current sweep.

Suggested first-pass settings for finding a strong transition signal:

- LCR function: `Ls-Q`
- Monitor 1: `Z`
- Monitor 2: `IAC`
- Frequencies: `100, 1k, 10k, 100k`
- Voltage levels: `0.1, 0.3, 1.0`
- Aperture: `FAST` for searching, `MED` or `SLOW` after the best region is known

For each wire, run a conservative low-current sweep first to confirm the coil,
wire, and supply wiring behave normally before trying the full transition range.

## Literature Cues

Varga-related AC susceptibility work on shape-memory microwires points toward
tracking the change in magnetic response through the martensitic transition
while sweeping excitation conditions. The 2025 ACS Materials Au paper with
Michal Varga and Rastislav Varga uses temperature-dependent magnetization and AC
magnetic susceptibility to study glass-coated Fe-Mn-Ga microwires. Older
microwire susceptibility spectroscopy work with Rastislav Varga uses amplitude
dependence of complex AC susceptibility to distinguish magnetization processes.

Useful starting points:

- https://doi.org/10.1021/acsmaterialsau.5c00113
- https://doi.org/10.1016/j.jnoncrysol.2006.12.075
- https://www.nature.com/articles/s41598-017-19032-z
