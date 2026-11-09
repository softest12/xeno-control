"""Raw HID access through Linux ``hidraw`` (no external dependencies).

Used for Alienware peripherals (mouse, headset). Their vendor protocols are
not public, so this module provides the building blocks: enumeration, report
descriptor parsing, live monitoring and sending output/feature reports. See
docs/REVERSE_ENGINEERING.md for how to map the protocol.
"""
from __future__ import annotations

import os
import select
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from . import sysfs

DELL_VID = 0x413C
ALIENWARE_VID = 0x187C
VENDOR_IDS = (DELL_VID, ALIENWARE_VID)


# --------------------------------------------------------------------------
# Report descriptor parsing
# --------------------------------------------------------------------------

@dataclass
class Collection:
    usage_page: int
    usage: int

    @property
    def vendor_defined(self) -> bool:
        return self.usage_page >= 0xFF00

    def __str__(self) -> str:
        names = {0x01: "Generic Desktop", 0x0C: "Consumer", 0x0B: "Telephony",
                 0x08: "LED", 0x07: "Keyboard", 0x06: "Generic Device"}
        page = "Vendor" if self.vendor_defined else names.get(self.usage_page, "Page")
        return f"{page} 0x{self.usage_page:04X}:0x{self.usage:02X}"


@dataclass
class DescriptorSummary:
    collections: list[Collection] = field(default_factory=list)
    # report id -> size in bytes (without the report-id byte)
    input_reports: dict[int, int] = field(default_factory=dict)
    output_reports: dict[int, int] = field(default_factory=dict)
    feature_reports: dict[int, int] = field(default_factory=dict)


def parse_report_descriptor(data: bytes) -> DescriptorSummary:
    """Minimal HID report descriptor walker (top-level collections + report sizes)."""
    out = DescriptorSummary()
    bits: dict[str, dict[int, int]] = {"in": {}, "out": {}, "feat": {}}
    usage_page = 0
    report_id = 0
    report_size = 0
    report_count = 0
    usages: list[int] = []
    depth = 0
    i = 0
    while i < len(data):
        prefix = data[i]
        if prefix == 0xFE:  # long item
            size = data[i + 1] if i + 1 < len(data) else 0
            i += 3 + size
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        typ = (prefix >> 2) & 0x03
        tag = prefix >> 4
        value = int.from_bytes(data[i + 1:i + 1 + size], "little")
        i += 1 + size

        if typ == 1:  # global
            if tag == 0x0:
                usage_page = value
            elif tag == 0x7:
                report_size = value
            elif tag == 0x8:
                report_id = value
            elif tag == 0x9:
                report_count = value
        elif typ == 2:  # local
            if tag == 0x0:
                usages.append(value)
        elif typ == 0:  # main
            if tag == 0xA:  # collection
                if depth == 0:
                    usage = usages[-1] if usages else 0
                    # 4-byte usage may embed the page
                    page = (usage >> 16) if usage > 0xFFFF else usage_page
                    out.collections.append(Collection(page, usage & 0xFFFF))
                depth += 1
            elif tag == 0xC:  # end collection
                depth = max(0, depth - 1)
            elif tag in (0x8, 0x9, 0xB):
                key = {0x8: "in", 0x9: "out", 0xB: "feat"}[tag]
                bits[key][report_id] = bits[key].get(report_id, 0) + report_size * report_count
            usages = []
    out.input_reports = {k: (v + 7) // 8 for k, v in bits["in"].items()}
    out.output_reports = {k: (v + 7) // 8 for k, v in bits["out"].items()}
    out.feature_reports = {k: (v + 7) // 8 for k, v in bits["feat"].items()}
    return out


# --------------------------------------------------------------------------
# Enumeration
# --------------------------------------------------------------------------

@dataclass
class HidrawDevice:
    node: str            # /dev/hidrawN
    vid: int
    pid: int
    name: str
    phys: str
    uniq: str
    interface: int | None
    descriptor: DescriptorSummary

    @property
    def id(self) -> str:
        return f"{self.vid:04x}:{self.pid:04x}"

    @property
    def has_vendor_collection(self) -> bool:
        return any(c.vendor_defined for c in self.descriptor.collections)


def _interface_number(device_dir: Path) -> int | None:
    # /sys/class/hidraw/hidrawN/device -> .../1-2:1.3/0003:413C:A529.0007
    try:
        usb_intf = device_dir.resolve().parent.name
    except OSError:
        return None
    if ":" in usb_intf and "." in usb_intf:
        try:
            return int(usb_intf.rsplit(".", 1)[1])
        except ValueError:
            return None
    return None


def enumerate_devices(vendor_ids: tuple[int, ...] | None = VENDOR_IDS) -> list[HidrawDevice]:
    base = sysfs.path("/sys/class/hidraw")
    if not base.is_dir():
        return []
    devices = []
    for entry in sorted(base.iterdir(), key=lambda p: (len(p.name), p.name)):
        dev_dir = entry / "device"
        uevent = sysfs.read(dev_dir / "uevent", "") or ""
        props = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        hid_id = props.get("HID_ID", "")
        try:
            _bus, vid_s, pid_s = hid_id.split(":")
            vid, pid = int(vid_s, 16), int(pid_s, 16)
        except ValueError:
            continue
        if vendor_ids is not None and vid not in vendor_ids:
            continue
        try:
            desc = parse_report_descriptor((dev_dir / "report_descriptor").read_bytes())
        except OSError:
            desc = DescriptorSummary()
        devices.append(HidrawDevice(
            node=f"/dev/{entry.name}", vid=vid, pid=pid,
            name=props.get("HID_NAME", "?"), phys=props.get("HID_PHYS", ""),
            uniq=props.get("HID_UNIQ", ""), interface=_interface_number(dev_dir),
            descriptor=desc,
        ))
    return devices


def resolve(target: str) -> list[HidrawDevice]:
    """Accept '/dev/hidrawN', 'hidrawN' or 'vid:pid' (all interfaces)."""
    if target.startswith("/dev/") or target.startswith("hidraw"):
        node = target if target.startswith("/dev/") else f"/dev/{target}"
        return [d for d in enumerate_devices(None) if d.node == node] or [
            HidrawDevice(node, 0, 0, "?", "", "", None, DescriptorSummary())]
    vid_s, pid_s = target.split(":")
    vid, pid = int(vid_s, 16), int(pid_s, 16)
    return [d for d in enumerate_devices(None) if d.vid == vid and d.pid == pid]


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def _hidiocsfeature(length: int) -> int:
    return _ioc(3, 0x06, length)


def _hidiocgfeature(length: int) -> int:
    return _ioc(3, 0x07, length)


def parse_hex(text: str) -> bytes:
    cleaned = text.replace(",", " ").replace("0x", " ").replace(":", " ")
    parts = cleaned.split()
    if len(parts) == 1 and len(parts[0]) > 2:
        return bytes.fromhex(parts[0])
    return bytes(int(p, 16) for p in parts)


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def send_output(node: str, data: bytes) -> int:
    """Write an output report. data[0] must be the report id (0 if none)."""
    fd = os.open(node, os.O_RDWR)
    try:
        return os.write(fd, data)
    finally:
        os.close(fd)


def send_feature(node: str, data: bytes) -> None:
    import fcntl  # Linux only
    buf = bytearray(data)
    fd = os.open(node, os.O_RDWR)
    try:
        fcntl.ioctl(fd, _hidiocsfeature(len(buf)), buf)
    finally:
        os.close(fd)


def get_feature(node: str, report_id: int, length: int) -> bytes:
    import fcntl  # Linux only
    buf = bytearray(length + 1)
    buf[0] = report_id
    fd = os.open(node, os.O_RDWR)
    try:
        fcntl.ioctl(fd, _hidiocgfeature(len(buf)), buf, True)
    finally:
        os.close(fd)
    return bytes(buf)


def monitor(nodes: list[str], duration: float | None = None,
            on_report: Callable[[str, bytes], None] | None = None) -> Iterator[tuple[float, str, bytes]]:
    """Yield (timestamp, node, data) for every input report on the given nodes."""
    fds = {}
    try:
        for node in nodes:
            fds[os.open(node, os.O_RDONLY | os.O_NONBLOCK)] = node
        start = time.monotonic()
        while duration is None or time.monotonic() - start < duration:
            ready, _, _ = select.select(list(fds), [], [], 0.25)
            for fd in ready:
                try:
                    data = os.read(fd, 1024)
                except BlockingIOError:
                    continue
                if on_report:
                    on_report(fds[fd], data)
                yield time.monotonic() - start, fds[fd], data
    finally:
        for fd in fds:
            os.close(fd)
