"""Known Alienware/Dell peripherals and the state of their protocol support.

Add new devices here. ``protocol`` points to a module in
``xeno_control/protocols`` once the vendor commands are mapped.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import hid


@dataclass(frozen=True)
class KnownDevice:
    name: str
    kind: str              # "headset" | "mouse" | "keyboard" | "receiver"
    status: str            # human-readable protocol status
    protocol: str | None = None


KNOWN: dict[tuple[int, int], KnownDevice] = {
    # USB product string: "AWPRO H Wireless". Vendor collection: page 0xFF13 (interface 5).
    (0x413C, 0xA529): KnownDevice(
        "Alienware Pro Wireless Gaming Headset", "headset",
        "áudio funciona nativo (USB Audio); bateria/EQ/sidetone: protocolo em mapeamento"),
    # USB product string: "Alienware Pro M Gaming Receiver".
    # Vendor collections: page 0xFF00 (interface 2) and 0xFF20 (interface 3).
    (0x413C, 0x2120): KnownDevice(
        "Alienware Pro Wireless Gaming Mouse (receptor)", "mouse",
        "mouse funciona nativo (HID); DPI/RGB/bateria: protocolo em mapeamento"),
}


@dataclass
class Peripheral:
    vid: int
    pid: int
    info: KnownDevice | None
    interfaces: list[hid.HidrawDevice]

    @property
    def title(self) -> str:
        if self.info:
            return self.info.name
        names = {i.name for i in self.interfaces}
        return next(iter(names)) if names else f"{self.vid:04x}:{self.pid:04x}"

    @property
    def vendor_nodes(self) -> list[str]:
        return [i.node for i in self.interfaces if i.has_vendor_collection]


def peripherals() -> list[Peripheral]:
    grouped: dict[tuple[int, int], list[hid.HidrawDevice]] = {}
    for dev in hid.enumerate_devices():
        grouped.setdefault((dev.vid, dev.pid), []).append(dev)
    return [Peripheral(vid, pid, KNOWN.get((vid, pid)), devs)
            for (vid, pid), devs in sorted(grouped.items())]
