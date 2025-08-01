#!/usr/bin/env python3
"""
max_heat.py

Force the ICN77530 to heat as fast as it can up to 100 °C by:

 1. Setting SP1 (the setpoint) to 100 °C in RAM only.
 2. Forcing MV (the manipulated variable/output) to 100 % in RAM only.

Nothing is ever written to EEPROM, and you can stop the script at any time
to restore normal operation.
"""

import serial
import time

# ─── CONFIGURATION ───────────────────────────────────────────────────────────────
PORT     = "COM14"               # ← change to your USB-serial port
BAUD     = 9600
BYTESIZE = serial.SEVENBITS      # 7 data bits
PARITY   = serial.PARITY_ODD     # odd parity
STOPBITS = serial.STOPBITS_ONE   # 1 stop bit
# ────────────────────────────────────────────────────────────────────────────────

def send_put_command(ser, code, value, dp=2):
    """
    Send a RAM-only Put command:
      code: two-digit string, e.g. "01" for SP1, "02" for MV
      value: float (°C or %)
      dp: decimal-point position code (2 = XXX.X mode)
    """
    # Convert to “counts” (e.g. 100.0°C → 1000 counts; 100.0 % → 1000 counts)
    counts = int(round(value * 10))
    # Build the 24-bit word: bit23=0 (positive), bits22-20 = dp, bits19-0 = counts
    header = (0 << 23) | (dp << 20)
    word   = header | counts
    payload = f"{word:06X}"              # 6-digit hex
    cmd     = f"*P{code}{payload}\r"     # “*Pxxhhhhhh<CR>”
    ser.write(cmd.encode('ascii'))
    ack = ser.read(10)
    return ack

def main():
    print(f"Opening port {PORT} @ {BAUD} baud…")
    ser = serial.Serial(
        PORT,
        baudrate=BAUD,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=1
    )

    try:
        # 1) Put SP1 = 100.0 °C (code “01”)
        print(" → Putting Setpoint-1 to 100.0 °C (RAM only)…")
        ack1 = send_put_command(ser, code="01", value=100.0)
        print(f"    Ack: {ack1!r}")

        # 2) Put MV = 100.0 % (code “02”) to force full power
        print(" → Forcing Output (MV) to 100 % (RAM only)…")
        ack2 = send_put_command(ser, code="02", value=100.0)
        print(f"    Ack: {ack2!r}")

        print("\n▶ Heater is now running at maximum power toward 100 °C.")
        print("  Press Ctrl+C when you want to stop forcing full power.")
        # Just sit here until the user stops the script
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nUser stopped the script—restoring MV back to auto mode…")
        # Optionally, you could send MV=0 here to relinquish power:
        send_put_command(ser, code="02", value=0.0)
        print(" → Output set to 0 % (heater off).")

    finally:
        ser.close()
        print("Serial port closed. Done.")

if __name__ == "__main__":
    main()
