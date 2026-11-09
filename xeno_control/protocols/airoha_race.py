"""Airoha RACE over USB HID (Alienware Pro Wireless Gaming Headset dongle).

The headset dongle (``413c:a529``, vendor page ``0xFF13``) tunnels Airoha's
RACE protocol through two HID reports:

* output report 6 (61 bytes): ``06 <len> <recipient> <RACE frame...>``
* input report 7 (61 bytes), read with GET_REPORT: ``07 <len> <recipient> <RACE stream...>``

``recipient`` 0x80 is the headset behind the dongle, 0x00 the dongle itself.
A RACE frame is ``05 <type> <u16le length> <u16le opcode> <payload>``, where
length counts opcode + payload; type 0x5A = request, 0x5B = response,
0x5D = indication. The input report keeps the last snapshot (the dongle does
not push on its interrupt endpoint), so we poll it and skip unchanged reads.

Battery (opcode 0x0CD6, verified on this headset): request payload ``[00]``;
the answer is a 0x5D indication with payload ``[status, role, percent]``.
Layout as documented by the Headroom / HaloBattery projects for other Airoha
dongles.

Only the battery read is implemented on purpose: the same channel reaches
flash erase and firmware update (opcodes 0x0400-0x04FF, 0x1C00-0x1CFF), so
this module never sends anything else.
"""
from __future__ import annotations

import fcntl
import os
import struct
import time
from dataclasses import dataclass
from typing import Callable

REPORT_OUT = 0x06
REPORT_IN = 0x07
REPORT_SIZE = 62          # report id + 61 bytes
RECIPIENT_HEADSET = 0x80

HEAD = 0x05
T_REQUEST, T_RESPONSE, T_INDICATION = 0x5A, 0x5B, 0x5D
OP_BATTERY = 0x0CD6


def _hidiocginput(length: int) -> int:
    return (3 << 30) | (length << 16) | (ord("H") << 8) | 0x0A


@dataclass(frozen=True)
class Frame:
    type: int
    opcode: int
    payload: bytes


def build_request(opcode: int, payload: bytes, recipient: int = RECIPIENT_HEADSET) -> bytes:
    frame = struct.pack("<BBHH", HEAD, T_REQUEST, len(payload) + 2, opcode) + payload
    return (bytes([REPORT_OUT, len(frame), recipient]) + frame).ljust(REPORT_SIZE, b"\x00")


def parse_frames(stream: bytes) -> list[Frame]:
    frames, i = [], 0
    while i + 6 <= len(stream):
        if stream[i] != HEAD:
            i += 1
            continue
        type_ = stream[i + 1]
        length, opcode = struct.unpack_from("<HH", stream, i + 2)
        end = i + 4 + length
        if length < 2 or end > len(stream):
            i += 1
            continue
        frames.append(Frame(type_, opcode, bytes(stream[i + 6:end])))
        i = end
    return frames


def report_payload(report: bytes) -> bytes:
    count = report[1] if len(report) > 1 else 0
    if not 0 < count <= REPORT_SIZE - 3:
        return b""
    return bytes(report[3:3 + count])


class Transport:
    """hidraw I/O. Tests replace it with a fake."""

    def __init__(self, node: str) -> None:
        self.fd = os.open(node, os.O_RDWR)

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def get_input(self) -> bytes:
        buf = bytearray(REPORT_SIZE)
        buf[0] = REPORT_IN
        fcntl.ioctl(self.fd, _hidiocginput(REPORT_SIZE), buf, True)
        return bytes(buf)

    def close(self) -> None:
        os.close(self.fd)


def read_battery(node: str, timeout: float = 2.0,
                 transport: Callable[[str], Transport] = Transport,
                 sleep: Callable[[float], None] = time.sleep) -> int | None:
    """Battery percent of the headset, or None if it did not answer (off/out of range)."""
    t = transport(node)
    try:
        last = t.get_input()  # stale snapshot: ignore until it changes
        t.write(build_request(OP_BATTERY, b"\x00"))
        stream = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            report = t.get_input()
            if report != last:
                last = report
                stream += report_payload(report)
                for f in parse_frames(stream):
                    if (f.type == T_INDICATION and f.opcode == OP_BATTERY
                            and len(f.payload) >= 3 and f.payload[0] == 0
                            and 0 <= f.payload[2] <= 100):
                        return f.payload[2]
            sleep(0.05)
        return None
    finally:
        t.close()
