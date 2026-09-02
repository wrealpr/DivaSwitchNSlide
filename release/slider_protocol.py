"""
slider_protocol.py - implements the SEGA touch slider serial protocol
(used by Project DIVA Arcade: Future Tone, among other titles) well enough
to pretend to be the physical peripheral over a serial port.

Protocol reference (public, community-documented):
    https://gist.github.com/dogtopus/b61992cfc383434deac5fab11a458597

This is an independent implementation written directly from that spec,
not copied from any existing project - it's included here so you have a
single, readable reference for how the framing/checksum actually works,
separate from the touch-to-zone mapping logic in the bridge script.

Wire format per packet (before byte-stuffing):
    [SYNC] [cmd] [len] [payload...] [checksum]
- SYNC (0xff) marks the start of a packet and is never escaped elsewhere.
- Any 0xff or 0xfd byte in cmd/len/payload/checksum is escaped as
  0xfd followed by (byte - 1).
- checksum = (-(sum of cmd, len, and payload bytes, unescaped)) % 256
  i.e. sum of [SYNC, cmd, len, *payload, checksum] % 256 == 0.
"""

import itertools
from typing import NamedTuple, Optional, Sequence

SYNC = 0xFF
ESCAPE = 0xFD

# Command IDs relevant to acting as the device (see protocol doc for full list)
CMD_SLIDER_REPORT = 0x01
CMD_LED_REPORT = 0x02
CMD_ENABLE_SLIDER_REPORT = 0x03
CMD_DISABLE_SLIDER_REPORT = 0x04
CMD_SET_SHORT_RAW_COUNT_OFFSET = 0x09
CMD_SET_SHORT_RAW_COUNT_SHIFTS = 0x0A
CMD_RESET = 0x10
CMD_EXCEPTION = 0xEE
CMD_GET_HW_INFO = 0xF0


class Packet(NamedTuple):
    command: int
    payload: bytes


def _escape_byte(b: int) -> Sequence[int]:
    if b in (SYNC, ESCAPE):
        return (ESCAPE, b - 1)
    return (b,)


def encode_packet(packet: Packet) -> bytes:
    """Build a full wire-format packet, including SYNC and escaping."""
    if len(packet.payload) > 255:
        raise ValueError("Payload too long (max 255 bytes)")

    unescaped = [packet.command, len(packet.payload)]
    unescaped.extend(packet.payload)
    checksum = (-(SYNC + sum(unescaped))) % 256
    unescaped.append(checksum)

    out = [SYNC]
    out.extend(itertools.chain.from_iterable(_escape_byte(b) for b in unescaped))
    return bytes(out)


class PacketDecoder:
    """Feed this one byte at a time (as read from the serial port); it
    returns a Packet once a complete, checksum-valid packet has arrived."""

    def __init__(self):
        self._buf = bytearray()
        self._target_len = 0
        self._escaped = False

    def _reset(self):
        self._buf.clear()
        self._target_len = 0
        self._escaped = False

    def feed(self, b: int) -> Optional[Packet]:
        if b == SYNC:
            # A SYNC always starts a fresh packet, even mid-stream.
            self._reset()
            self._buf.append(b)
            return None

        if not self._buf:
            # Haven't seen a SYNC yet; ignore stray bytes.
            return None

        if self._escaped:
            self._buf.append(b + 1)
            self._escaped = False
        elif b == ESCAPE:
            self._escaped = True
            return None
        else:
            self._buf.append(b)

        # buf[0]=SYNC, buf[1]=cmd, buf[2]=len -> total length = len + 4
        if len(self._buf) == 3:
            self._target_len = self._buf[2] + 4
        elif len(self._buf) > 3 and len(self._buf) == self._target_len:
            packet = None
            if sum(self._buf) % 256 == 0:
                packet = Packet(self._buf[1], bytes(self._buf[3:-1]))
            # else: bad checksum, drop silently and wait for next SYNC
            self._reset()
            return packet

        return None
