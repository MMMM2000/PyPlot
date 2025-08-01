#!/usr/bin/env python3
"""
bench_volt.py

Control a 0–32 V bench supply (Statron 3231.x) via the ICN77530’s 0–10 V output.
Nothing is written to EEPROM—uses the “Put MV” (*P02…) command to send 0–100 %,
which maps to 0–10 V on the controller, and thus 0–32 V on the supply.
"""

import serial
import argparse

# ─── CONFIG ──────────────────────────────────────────────────────────────────────
DEFAULT_PORT = "COM14"   # ← change to your serial port
BAUD         = 9600      # your controller’s serial baud
BYTESIZE     = serial.SEVENBITS
PARITY       = serial.PARITY_ODD
STOPBITS     = serial.STOPBITS_ONE

MAX_SUPPLY_V = 32.0      # supply’s max voltage at 10 V remote
# ────────────────────────────────────────────────────────────────────────────────

def put_mv(ser, percent):
    """Send *P02… to set MV = percent%, i.e. analog out = percent/100*10 V."""
    if not (0 <= percent <= 100):
        raise ValueError("percent must be 0…100")
    counts = int(round(percent * 10))           # 0…1000 counts
    header = (0 << 23) | (2 << 20)              # sign=0, dp=2 (XXX.X)
    word   = header | counts
    payload= f"{word:06X}"
    cmd    = f"*P02{payload}\r"
    ser.write(cmd.encode("ascii"))
    ack = ser.read(16)
    print(f"> MV → {percent:5.1f}%  payload={payload}  ack={ack!r}")

def main():
    p = argparse.ArgumentParser(
        description="Drive Statron bench supply voltage via ICN77530 analog out"
    )
    p.add_argument("--port", default=DEFAULT_PORT,
                   help="Serial port (e.g. COM14 or /dev/ttyUSB0)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--voltage", type=float,
                   help=f"Desired supply voltage (0…{MAX_SUPPLY_V} V)")
    g.add_argument("--percent", type=float,
                   help="Direct percent (0…100) of analog output")
    args = p.parse_args()

    if args.voltage is not None:
        pct = args.voltage / MAX_SUPPLY_V * 100.0
    else:
        pct = args.percent

    # clamp
    if pct < 0: pct = 0
    if pct > 100: pct = 100

    print(f"Opening {args.port} @ {BAUD} baud…")
    ser = serial.Serial(
        args.port,
        baudrate=BAUD,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=1
    )

    try:
        put_mv(ser, pct)
    finally:
        ser.close()
        print("Port closed.")

if __name__ == "__main__":
    main()
