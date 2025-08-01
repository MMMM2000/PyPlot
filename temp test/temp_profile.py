#!/usr/bin/env python3
"""
temp_profile.py

Step a stepped temperature profile (25 → 40 → 60 → 80 → 100 °C) on an ICN77530 controller
via RS-232/485 using the pyserial library.
"""

import serial
import time

# ─── CONFIG ──────────────────────────────────────────────────────────────────────
# Change this to whatever COM port your USB-serial adapter uses:
PORT = 'COM14'

# Controller serial settings:
BAUD = 9600               # baud rate
BYTESIZE = serial.SEVENBITS  # 7 data bits
PARITY = serial.PARITY_ODD   # odd parity
STOPBITS = serial.STOPBITS_ONE  # 1 stop bit

# The temperatures (°C) and hold time (seconds) for your profile:
PROFILE = [
    (25,  3),  # (temperature, hold-time)
    (40,  3),
    (60,  3),
    (80,  3),
    (100, 3),
]
# ────────────────────────────────────────────────────────────────────────────────

def set_temperature(ser, celsius):
    """
    Send a Setpoint-1 write command (*W01…) to choose 'celsius' on the controller.
    Assumes the controller is set for XXX.X mode (one decimal place).
    """
    # 1) Convert °C to “counts” (e.g. 25.0°C -> 250)
    counts = int(round(celsius * 10))

    # 2) Build the 24-bit word:
    #    Bit23 = sign (0 = positive)
    #    Bits22-20 = decimal point code (2 = XXX.X)
    header = (0 << 23) | (2 << 20)
    word = header | counts

    # 3) Make a 6-digit hex payload (pad with zeros)
    payload = f"{word:06X}"  # e.g. '083FA' for 25.0°C

    # 4) Form the ASCII command and send it
    cmd = f"*W01{payload}\r"
    ser.write(cmd.encode('ascii'))

    # 5) (Optional) read back an ACK message
    ack = ser.read(10)
    print(f" → Sent {celsius}°C, payload={payload}, ack={ack!r}")

def main():
    # Open the serial port
    print(f"Opening serial port {PORT} @ {BAUD} baud…")
    ser = serial.Serial(
        PORT,
        baudrate=BAUD,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=1
    )

    # Loop through each step in the PROFILE
    for temp, hold in PROFILE:
        print(f"\nSetting temperature to {temp}°C…")
        set_temperature(ser, temp)

        print(f"Holding at {temp}°C for {hold//60} minutes ({hold} s)…")
        time.sleep(hold)

    print("\nProfile complete! Closing serial port.")
    ser.close()

if __name__ == "__main__":
    main()
