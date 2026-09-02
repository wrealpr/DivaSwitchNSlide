#!/usr/bin/env python3
"""
switch_slider_bridge.py - makes PDAFT (Project DIVA Arcade: Future Tone)
think a real touch slider is connected, driven by touch input from a
Nintendo Switch running the touch_capture homebrew app over USB.

Requires:
    pip install pyusb pyserial

Setup:
1. A virtual null-modem serial port pair, e.g. via com0com on Windows.
   Bind this script to one end (e.g. COM12) and configure PDAFT to use
   the other end (e.g. COM11) - this mirrors the setup documented by the
   PD-OSS project, which does the same thing from mouse/touch input:
   https://github.com/twisteroidambassador/pd-oss
2. touch_capture.nro running on the Switch, connected over USB.

What this does NOT do: modify, patch, or inject into PDAFT in any way.
It only impersonates a serial peripheral - the same category of thing as
plugging in a real (or third-party) slider controller.

Zone mapping: the physical 15275 slider has 32 zones, left-to-right
(per the protocol doc). This bridge splits the Switch's touch x-range
(0-1279, the console's screen width in touch coordinates) into 32 equal
buckets and marks a zone "touched" if any active touch point falls in
its bucket. This is a starting assumption, not a verified calibration -
you'll likely want to tune SWITCH_TOUCH_X_MAX or add left/right margins
once you can see how it feels in-game.
"""

import sys
import threading
import time
import subprocess

import serial
import usb.core
import usb.util

from slider_protocol import (
    Packet,
    PacketDecoder,
    encode_packet,
    CMD_SLIDER_REPORT,
    CMD_LED_REPORT,
    CMD_ENABLE_SLIDER_REPORT,
    CMD_DISABLE_SLIDER_REPORT,
    CMD_SET_SHORT_RAW_COUNT_OFFSET,
    CMD_SET_SHORT_RAW_COUNT_SHIFTS,
    CMD_RESET,
    CMD_GET_HW_INFO,
)

# --- USB (Switch touch_capture) settings ---
NINTENDO_VID = 0x057E
NXLINK_PID = 0x3000  # see pc_receiver.py notes - verify against your device

# --- Serial (fake slider device) settings ---
SLIDER_COM_PORT = "COM12"  # the end of the null-modem pair THIS script uses
SLIDER_BAUD = 115200

# --- Touch-to-zone mapping ---
NUM_ZONES = 32
SWITCH_TOUCH_X_MAX = 1280  # console's touch coordinate space is 1280 wide
ZONE_TOUCHED_VALUE = 0xFE
ZONE_IDLE_VALUE = 0x00

# If slide notes register as "wrong direction" even though you're tracking
# them correctly on-screen, that's almost always this being backwards -
# flip it and retry before looking anywhere else.
REVERSE_ZONE_MAPPING = False

# Canned GetHWInfo response identifying as a real 15275 (Diva) slider board,
# taken directly from the example decoded packet in the protocol doc.
HW_INFO_PAYLOAD = b"15275   " + bytes([0xA0]) + b"06687" + bytes([0xFF, 0x90, 0x00, 0x64])

# --- Global hotkey settings ---
ESCAPE_DOUBLE_TAP_MS = 424
ESC_KEY_CODE = 0x1B  # Virtual key code for Escape


class TouchZoneState:
    """Thread-safe holder for "which of the 32 zones are currently touched,"
    with a short hold-over grace period. Without this, a single momentary
    dropout in a polling cycle (whether from sensor noise at the touch
    panel's ~250Hz internal rate, or from how USB data happens to get
    chunked into frames on our end) instantly zeroes the zone - which
    looks exactly like the flicker seen in the Input Test screen. This
    only releases a zone after it's been genuinely absent for a bit,
    not on every single blip."""

    GRACE_PERIOD_S = 0.030  # raise if flicker persists, lower if release
                             # feels laggy/sticky once this fixes it

    def __init__(self):
        self._lock = threading.Lock()
        self._zone_last_seen = [0.0] * NUM_ZONES

    def set_from_touches(self, touch_xs):
        now = time.time()
        zones_this_update = [False] * NUM_ZONES
        with self._lock:
            for x in touch_xs:
                zone = int(x / SWITCH_TOUCH_X_MAX * NUM_ZONES)
                zone = max(0, min(NUM_ZONES - 1, zone))
                if REVERSE_ZONE_MAPPING:
                    zone = (NUM_ZONES - 1) - zone
                self._zone_last_seen[zone] = now
                zones_this_update[zone] = True
        self._print_bar(zones_this_update)

    def _print_bar(self, zones):
        # Live debug view: which zone indices WE think are active right now.
        # Compare this directly against whatever PDAFT's own Service Menu /
        # Input Test screen shows for the same touch - no need to know what
        # a "zone" means, just compare the two numbers you see.
        bar = "".join("X" if z else "." for z in zones)
        active = [i for i, z in enumerate(zones) if z]
        print(f"\rzones: [{bar}] active={active}    ", end="", flush=True)

    def snapshot(self) -> bytes:
        now = time.time()
        with self._lock:
            return bytes(
                ZONE_TOUCHED_VALUE if (now - last_seen) < self.GRACE_PERIOD_S
                else ZONE_IDLE_VALUE
                for last_seen in self._zone_last_seen
            )


def find_switch_usb_device():
    dev = usb.core.find(idVendor=NINTENDO_VID, idProduct=NXLINK_PID)
    if dev is None:
        print("Switch not found over USB. Is touch_capture running and cable connected?")
        print("Running keepalive.py instead...")
        subprocess.run([sys.executable, "keepalive.py"])
        sys.exit(0)
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    ep_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_IN,
    )
    if ep_in is None:
        print("Couldn't find an IN endpoint on the Switch USB device.")
        sys.exit(1)
    return ep_in


def usb_reader_thread(ep_in, zone_state: TouchZoneState, stop_event: threading.Event):
    """Reads touch_capture's line-based USB stream and updates zone_state."""
    buf = b""
    active_touch_xs = {}  # finger_id -> x, cleared each "F" frame marker

    while not stop_event.is_set():
        try:
            data = ep_in.read(4096, timeout=1000)
            buf += bytes(data)
        except usb.core.USBError as e:
            if e.errno == 110:  # timeout
                continue
            print(f"USB read error: {e}")
            continue

        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            parts = line.decode(errors="replace").split()
            if not parts:
                continue
            if parts[0] == "F":
                # New polled frame starting - touches not re-reported this
                # frame have lifted, so drop anything not refreshed below.
                active_touch_xs.clear()
            elif parts[0] == "T" and len(parts) >= 3:
                finger_id, x = parts[1], parts[2]
                active_touch_xs[finger_id] = int(x)

        zone_state.set_from_touches(active_touch_xs.values())


class SliderDeviceResponder:
    """Speaks the slider protocol on a serial port, acting as the device."""

    REPORT_INTERVAL = 0.012  # ~83Hz, matches the documented real hardware rate

    def __init__(self, port: str, baud: int, zone_state: TouchZoneState):
        self._ser = serial.Serial(port, baud, timeout=0.1)
        self._decoder = PacketDecoder()
        self._zone_state = zone_state
        self._stop = threading.Event()
        self._reporting = threading.Event()  # set once host sends EnableSliderReport
        self._report_thread = threading.Thread(target=self._report_loop, daemon=True)

    def start(self):
        self._report_thread.start()
        self._read_loop()  # blocks; run this on the main thread or its own

    def stop(self):
        self._stop.set()
        self._reporting.clear()
        self._report_thread.join(timeout=1)
        self._ser.close()

    def _write(self, packet: Packet, log=True):
        self._ser.write(encode_packet(packet))
        if log:
            print(f"TX: cmd=0x{packet.command:02x} payload={packet.payload!r}")

    def _read_loop(self):
        while not self._stop.is_set():
            data = self._ser.read(1)
            if not data:
                continue
            packet = self._decoder.feed(data[0])
            if packet is not None:
                if packet.command != CMD_LED_REPORT:
                    print(f"RX: cmd=0x{packet.command:02x} payload={packet.payload!r}")
                self._handle_packet(packet)

    def _handle_packet(self, packet: Packet):
        if packet.command == CMD_RESET:
            self._reporting.clear()
            self._write(Packet(CMD_RESET, b""))
        elif packet.command == CMD_GET_HW_INFO:
            self._write(Packet(CMD_GET_HW_INFO, HW_INFO_PAYLOAD))
        elif packet.command == CMD_SET_SHORT_RAW_COUNT_OFFSET:
            # Host is configuring debug offset/shift values as part of its
            # normal init sequence; we don't use raw counts at all, but we
            # still have to ack it or the handshake stalls here.
            self._write(Packet(CMD_SET_SHORT_RAW_COUNT_OFFSET, b""))
        elif packet.command == CMD_SET_SHORT_RAW_COUNT_SHIFTS:
            self._write(Packet(CMD_SET_SHORT_RAW_COUNT_SHIFTS, b""))
        elif packet.command == CMD_ENABLE_SLIDER_REPORT:
            print("Host enabled slider reporting - starting periodic reports.")
            self._reporting.set()
        elif packet.command == CMD_DISABLE_SLIDER_REPORT:
            self._reporting.clear()
            self._write(Packet(CMD_DISABLE_SLIDER_REPORT, b""))
        elif packet.command == CMD_LED_REPORT:
            pass  # no physical LEDs to drive; acknowledge by ignoring
        else:
            print(f"Unhandled command 0x{packet.command:02x}, ignoring.")

    def _report_loop(self):
        while not self._stop.is_set():
            if self._reporting.is_set():
                self._write(
                    Packet(CMD_SLIDER_REPORT, self._zone_state.snapshot()), log=False
                )
            time.sleep(self.REPORT_INTERVAL)


def check_escape_quit():
    """Check for double-tap Escape using GetAsyncKeyState from Windows API.
    Returns True if Escape was pressed twice within ESCAPE_DOUBLE_TAP_MS."""
    import ctypes
    from ctypes import wintypes
    
    user32 = ctypes.windll.user32
    # GetAsyncKeyState returns 0x8000 if key is currently pressed
    # and 0x0001 if key was pressed since last call
    GET_ASYNC_KEY_STATE = user32.GetAsyncKeyState
    GET_ASYNC_KEY_STATE.argtypes = [wintypes.INT]
    GET_ASYNC_KEY_STATE.restype = wintypes.SHORT
    
    # Static state for the double-tap detector
    if not hasattr(check_escape_quit, "_last_press_time"):
        check_escape_quit._last_press_time = 0
        check_escape_quit._last_state = 0
    
    current_state = GET_ASYNC_KEY_STATE(ESC_KEY_CODE)
    
    # Check if key was just pressed (bit 0 set, meaning it was pressed since last call)
    if current_state & 0x0001:
        current_time = time.time() * 1000  # milliseconds
        if check_escape_quit._last_press_time > 0:
            elapsed = current_time - check_escape_quit._last_press_time
            if elapsed <= ESCAPE_DOUBLE_TAP_MS:
                return True
        check_escape_quit._last_press_time = current_time
    
    return False


def main():
    print("Looking for Switch over USB...")
    ep_in = find_switch_usb_device()
    print("Found it. Starting touch reader...")

    zone_state = TouchZoneState()
    stop_event = threading.Event()
    usb_thread = threading.Thread(
        target=usb_reader_thread, args=(ep_in, zone_state, stop_event), daemon=True
    )
    usb_thread.start()

    print(f"Opening {SLIDER_COM_PORT} as the fake slider device...")
    print("Waiting for PDAFT to talk to it (launch/focus the game now).")
    print("Double-tap Escape (twice within 424ms) to quit.")
    
    responder = SliderDeviceResponder(SLIDER_COM_PORT, SLIDER_BAUD, zone_state)
    try:
        # Run the responder on a separate thread so we can check for Escape
        responder_thread = threading.Thread(target=responder.start, daemon=True)
        responder_thread.start()
        
        # Main thread monitors for Escape key while the responder runs
        while responder_thread.is_alive():
            if check_escape_quit():
                print("\nEscape double-tap detected - shutting down...")
                break
            time.sleep(0.01)  # Small sleep to prevent CPU spinning
            
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        responder.stop()
        print("Shutdown complete.")


if __name__ == "__main__":
    main()