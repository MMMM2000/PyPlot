#!/usr/bin/env python3
"""
temp_profile_volatile.py

Step a volatile (RAM-only) temperature profile (25 → 40 → 60 → 80 → 100 °C)
on an ICN77530 controller via RS-232/485 using the pyserial library.
This uses the *P01… “Put” command so nothing is written to EEPROM.
"""

import serial
import time

# ─── CONFIG ──────────────────────────────────────────────────────────────────────
PORT     = 'COM14'                # your USB-serial port
BAUD     = 9600
BYTESIZE = serial.SEVENBITS       # 7 data bits
PARITY   = serial.PARITY_ODD      # odd parity
STOPBITS = serial.STOPBITS_ONE    # 1 stop bit

# Profile: list of (°C, hold_seconds)
PROFILE = [
    (25,  3),
    (40,  3),
    (60,  3),
    (80,  3),
    (100, 3),
]
# ────────────────────────────────────────────────────────────────────────────────

def put_temperature(ser, celsius):
    """
    Send a volatile Setpoint-1 “Put” command (*P01…) so it only updates RAM.
    """
    counts = int(round(celsius * 10))
    header = (0 << 23) | (2 << 20)       # sign=0, dp=2 (XXX.X)
    word   = header | counts
    payload= f"{word:06X}"

    cmd = f"*P01{payload}\r"            # <-- note the ‘P’ instead of ‘W’
    ser.write(cmd.encode('ascii'))

    ack = ser.read(10)
    print(f" → Put {celsius}°C, payload={payload}, ack={ack!r}")

def main():
    print(f"Opening {PORT} @ {BAUD}…")
    ser = serial.Serial(
        PORT, baudrate=BAUD,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=1
    )

    for temp, hold in PROFILE:
        print(f"\nPutting temperature to {temp}°C…")
        put_temperature(ser, temp)
        print(f"Holding at {temp}°C for {hold//60} min…")
        time.sleep(hold)

    print("\nProfile complete. Closing port.")
    ser.close()

if __name__ == "__main__":
    main()
