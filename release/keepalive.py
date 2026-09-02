#!/usr/bin/env python3
"""
keepalive.py - the minimum needed to stop PDAFT from hanging on its load
screen when the com0com COM11/COM12 pair exists.

PDAFT's startup hardware probe appears to need *something* on the other
end of COM11 (i.e. COM12 held open) to not hang, independent of whether
Hardware_Slider is even turned on. This doesn't implement the slider
protocol at all - it just keeps the port open so the probe doesn't stall.
"""

import serial

PORT = "COM12"
BAUD = 115200

print(f"Holding {PORT} open so PDAFT doesn't hang on startup.")
print("Leave this running, then launch PDAFT. Ctrl+C to stop.")

ser = serial.Serial(PORT, BAUD, timeout=1)
try:
    while True:
        ser.read(64)  # discard anything sent; we're not responding to it
except KeyboardInterrupt:
    pass
finally:
    ser.close()
