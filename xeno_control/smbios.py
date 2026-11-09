"""Battery charge control through the ``dell-smbios`` WMI interface.

``dell-laptop`` exposes ``charge_types`` and charge thresholds in sysfs, but it
refuses to load when the DMI vendor is "Alienware" instead of "Dell Inc."
(``modprobe: ERROR: could not insert 'dell_laptop': No such device``). The
firmware still implements the same Dell SMBIOS tokens, so we talk to it the way
``dell-laptop`` and libsmbios' ``smbios-battery-ctl`` do:

* token location/value come from ``/sys/bus/platform/devices/dell-smbios.0/tokens``;
* calls go through the ``DELL_WMI_SMBIOS_CMD`` ioctl on ``/dev/wmi/dell-smbios``.

Both need root (CAP_SYS_ADMIN), so reads as a normal user raise PermissionError.
"""
from __future__ import annotations

import fcntl
import struct
from dataclasses import dataclass

from . import sysfs

DEVICE = "/dev/wmi/dell-smbios"
TOKENS_DIR = "/sys/bus/platform/devices/dell-smbios.0/tokens"

CLASS_TOKEN_READ = 0
CLASS_TOKEN_WRITE = 1
SELECT_TOKEN_STD = 0

# Names match the ones dell-laptop writes to ``charge_types``.
MODE_TOKENS = {
    "Standard": 0x0346,
    "Adaptive": 0x0342,
    "Fast": 0x0347,        # "Express" no AWCC/BIOS
    "Custom": 0x0343,
    "Long_Life": 0x0341,   # "Primarily AC"
}
CUSTOM_START = 0x0349
CUSTOM_END = 0x034A

# struct dell_wmi_smbios_buffer: u64 length; calling_interface_buffer std
# (u16 class, u16 select, u32 input[4], u32 output[4]); extensions (u32, u32, data[]).
_STD_FMT = "<HH4I4i"
_STD_OFFSET = 8
_STRUCT_SIZE = 8 + struct.calcsize(_STD_FMT) + 8  # 52
# _IOWR('W', 0, struct dell_wmi_smbios_buffer)
DELL_WMI_SMBIOS_CMD = (3 << 30) | (_STRUCT_SIZE << 16) | (ord("W") << 8) | 0


class SmbiosError(RuntimeError):
    pass


@dataclass(frozen=True)
class Token:
    id: int
    location: int
    value: int


def _tokens_dir():
    return sysfs.path(TOKENS_DIR)


def available() -> bool:
    """The interface exists and the firmware has the battery mode tokens."""
    tokens = _tokens_dir()
    return sysfs.path(DEVICE).exists() and (tokens / f"{MODE_TOKENS['Custom']:04x}_location").exists()


def has_token(token_id: int) -> bool:
    return (_tokens_dir() / f"{token_id:04x}_location").exists()


def supported_modes() -> list[str]:
    """Modes whose token exists. Listing the directory works without root."""
    return [name for name, tid in MODE_TOKENS.items() if has_token(tid)]


def token(token_id: int) -> Token:
    base = _tokens_dir()
    try:
        location = (base / f"{token_id:04x}_location").read_text().strip()
        value = (base / f"{token_id:04x}_value").read_text().strip()
    except FileNotFoundError:
        raise SmbiosError(f"token 0x{token_id:04x} não existe nesta BIOS") from None
    return Token(token_id, int(location, 16), int(value, 16))


def call(cmd_class: int, cmd_select: int, inputs: tuple[int, ...]) -> tuple[int, ...]:
    """Run one SMBIOS call. Returns ``output[0..3]`` (output[0] < 0 = error)."""
    with open(sysfs.path(DEVICE), "rb", buffering=0) as dev:
        size = struct.unpack("<Q", dev.read(8))[0]
        buf = bytearray(max(size, _STRUCT_SIZE))
        struct.pack_into("<Q", buf, 0, len(buf))
        args = (list(inputs) + [0, 0, 0, 0])[:4]
        struct.pack_into(_STD_FMT, buf, _STD_OFFSET, cmd_class, cmd_select, *args, 0, 0, 0, 0)
        fcntl.ioctl(dev, DELL_WMI_SMBIOS_CMD, buf, True)
    return struct.unpack_from(_STD_FMT, buf, _STD_OFFSET)[6:]


def _checked(cmd_class: int, token_id: int, value: int = 0) -> tuple[int, ...]:
    tok = token(token_id)
    out = call(cmd_class, SELECT_TOKEN_STD, (tok.location, value))
    if out[0] != 0:
        raise SmbiosError(f"BIOS recusou a chamada no token 0x{token_id:04x} (código {out[0]})")
    return out


def read_token(token_id: int) -> int:
    return _checked(CLASS_TOKEN_READ, token_id)[1]


def write_token(token_id: int, value: int) -> None:
    _checked(CLASS_TOKEN_WRITE, token_id, value)


# --------------------------------------------------------------------------
# Battery
# --------------------------------------------------------------------------

def charge_mode() -> str | None:
    for name, tid in MODE_TOKENS.items():
        if has_token(tid) and read_token(tid) == token(tid).value:
            return name
    return None


ALIASES = {"express": "Fast", "primarily_ac": "Long_Life", "primarilyac": "Long_Life"}


def _canonical(name: str) -> str:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    if key in ALIASES:
        return ALIASES[key]
    for mode in MODE_TOKENS:
        if mode.lower() == key:
            return mode
    return name


def set_charge_mode(name: str) -> None:
    name = _canonical(name)
    if name not in MODE_TOKENS or not has_token(MODE_TOKENS[name]):
        raise ValueError(f"Modo inválido. Opções: {', '.join(supported_modes())}")
    tid = MODE_TOKENS[name]
    # Selection tokens are activated by writing their own value.
    write_token(tid, token(tid).value)


def thresholds() -> tuple[int, int]:
    return read_token(CUSTOM_START), read_token(CUSTOM_END)


def validate_thresholds(start: int, end: int) -> None:
    # Same limits dell-laptop and smbios-battery-ctl enforce for the firmware.
    if not 50 <= start <= 95:
        raise ValueError("início deve estar entre 50 e 95%")
    if not 55 <= end <= 100:
        raise ValueError("fim deve estar entre 55 e 100%")
    if end - start < 5:
        raise ValueError("fim precisa ser pelo menos 5 pontos maior que o início")


def set_thresholds(start: int, end: int) -> None:
    validate_thresholds(start, end)
    cur_start, cur_end = thresholds()
    # Avoid a transient state the firmware rejects (start >= end - 5).
    if start >= cur_end - 5:
        write_token(CUSTOM_END, end)
        write_token(CUSTOM_START, start)
    else:
        write_token(CUSTOM_START, start)
        write_token(CUSTOM_END, end)
