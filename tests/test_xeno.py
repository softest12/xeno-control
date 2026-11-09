"""Tests against a fake sysfs tree (runs on any OS)."""
from __future__ import annotations

from pathlib import Path

import pytest

from xeno_control import cli, devices, hid, laptop


def _w(root: Path, rel: str, content: str | bytes) -> Path:
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content)
    return p


# Report descriptor: vendor page 0xFF00 collection, report id 2, 63-byte in/out
VENDOR_DESC = bytes([
    0x06, 0x00, 0xFF,  # Usage Page (Vendor 0xFF00)
    0x09, 0x01,        # Usage 1
    0xA1, 0x01,        # Collection (Application)
    0x85, 0x02,        #   Report ID 2
    0x75, 0x08,        #   Report Size 8
    0x95, 0x3F,        #   Report Count 63
    0x09, 0x01, 0x81, 0x02,  # Input
    0x09, 0x01, 0x91, 0x02,  # Output
    0x95, 0x10,
    0x09, 0x01, 0xB1, 0x02,  # Feature 16 bytes
    0xC0,
])


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("XENO_SYSFS_ROOT", str(tmp_path))
    _w(tmp_path, "/sys/class/dmi/id/sys_vendor", "Alienware\n")
    _w(tmp_path, "/sys/class/dmi/id/product_name", "Alienware 16 Aurora AC16250\n")
    _w(tmp_path, "/sys/module/alienware_wmi/parameters/force_gmode", "N\n")
    _w(tmp_path, "/sys/firmware/acpi/platform_profile", "balanced\n")
    _w(tmp_path, "/sys/firmware/acpi/platform_profile_choices",
       "low-power quiet balanced balanced-performance performance custom\n")
    hw = "/sys/class/hwmon/hwmon5"
    _w(tmp_path, f"{hw}/name", "alienware_wmi\n")
    _w(tmp_path, f"{hw}/fan1_input", "2400\n")
    _w(tmp_path, f"{hw}/fan1_label", "CPU Fan\n")
    _w(tmp_path, f"{hw}/fan1_boost", "0\n")
    _w(tmp_path, f"{hw}/temp1_input", "61000\n")
    _w(tmp_path, f"{hw}/temp1_label", "CPU\n")
    bat = "/sys/class/power_supply/BAT0"
    _w(tmp_path, f"{bat}/type", "Battery\n")
    _w(tmp_path, f"{bat}/capacity", "87\n")
    _w(tmp_path, f"{bat}/status", "Charging\n")
    _w(tmp_path, f"{bat}/charge_types", "[Standard] Adaptive Fast Custom Long_Life\n")
    _w(tmp_path, f"{bat}/charge_control_start_threshold", "50\n")
    _w(tmp_path, f"{bat}/charge_control_end_threshold", "100\n")
    hr = "/sys/class/hidraw/hidraw3/device"
    _w(tmp_path, f"{hr}/uevent", "HID_ID=0003:0000413C:0000A529\nHID_NAME=Dell AWPRO H Wireless\n")
    _w(tmp_path, f"{hr}/report_descriptor", VENDOR_DESC)
    return tmp_path


def test_profiles(fake):
    assert laptop.current_profile() == "balanced"
    assert "performance" in laptop.profile_choices()
    laptop.set_profile("performance")
    assert (fake / "sys/firmware/acpi/platform_profile").read_text() == "performance"
    assert laptop.load_state()["profile"] == "performance"
    with pytest.raises(ValueError):
        laptop.set_profile("turbo")


def test_class_handler_preferred(fake):
    _w(fake, "/sys/class/platform-profile/platform-profile-0/name", "alienware-wmi\n")
    _w(fake, "/sys/class/platform-profile/platform-profile-0/profile", "quiet\n")
    _w(fake, "/sys/class/platform-profile/platform-profile-0/choices", "quiet performance\n")
    assert laptop.current_profile() == "quiet"
    assert laptop.profile_choices() == ["quiet", "performance"]


def test_sensors_and_fan(fake):
    sensors = {(s.kind, s.index): s for s in laptop.read_sensors()}
    assert sensors[("fan", 1)].value == 2400
    assert sensors[("temp", 1)].value == 61.0
    assert sensors[("boost", 1)].label == "CPU Fan boost"
    laptop.set_fan_boost(1, 128)
    assert (fake / "sys/class/hwmon/hwmon5/fan1_boost").read_text() == "128"
    with pytest.raises(ValueError):
        laptop.set_fan_boost(1, 300)


def test_battery(fake):
    (bat,) = laptop.batteries()
    assert bat.charge_type == "Standard"
    assert bat.capacity == 87
    laptop.set_charge_thresholds(60, 80)
    base = fake / "sys/class/power_supply/BAT0"
    assert (base / "charge_control_end_threshold").read_text() == "80"
    assert (base / "charge_control_start_threshold").read_text() == "60"
    assert (base / "charge_types").read_text() == "Custom"


def test_descriptor_parser():
    d = hid.parse_report_descriptor(VENDOR_DESC)
    assert d.collections[0].vendor_defined
    assert d.input_reports == {2: 63}
    assert d.output_reports == {2: 63}
    assert d.feature_reports == {2: 16}


def test_enumerate_and_known(fake):
    (dev,) = hid.enumerate_devices()
    assert dev.id == "413c:a529"
    assert dev.has_vendor_collection
    (per,) = devices.peripherals()
    assert per.info and per.info.kind == "headset"


def test_parse_hex():
    assert hid.parse_hex("02 0a ff") == b"\x02\x0a\xff"
    assert hid.parse_hex("020aff") == b"\x02\x0a\xff"


def test_cli_status(fake, capsys):
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "AC16250" in out and "balanced" in out and "Headset" in out


# --------------------------------------------------------------------------
# dell-smbios fallback (Alienware: dell-laptop refuses to load)
# --------------------------------------------------------------------------

from xeno_control import smbios  # noqa: E402

TOKENS = {0x0341: (0x10, 1), 0x0342: (0x10, 2), 0x0343: (0x10, 3), 0x0346: (0x10, 6),
          0x0347: (0x10, 7), 0x0349: (0x20, 0), 0x034A: (0x21, 0)}


class FakeFirmware:
    """Emulates the BIOS side of token read/write calls."""

    def __init__(self):
        self.mem = {0x10: 2, 0x20: 50, 0x21: 100}  # Adaptive, 50-100%
        self.calls = []

    def __call__(self, cls, sel, inputs):
        loc, val = inputs[0], inputs[1]
        self.calls.append((cls, sel, loc, val))
        if cls == smbios.CLASS_TOKEN_READ:
            return (0, self.mem[loc], 0, 0)
        if loc in (0x20, 0x21):
            start = val if loc == 0x20 else self.mem[0x20]
            end = val if loc == 0x21 else self.mem[0x21]
            if end - start < 5:
                return (-1, 0, 0, 0)
        self.mem[loc] = val
        return (0, 0, 0, 0)


@pytest.fixture
def alienware_bat(fake, monkeypatch):
    base = fake / "sys/class/power_supply/BAT0"
    for f in ("charge_types", "charge_control_start_threshold", "charge_control_end_threshold"):
        (base / f).unlink()
    for tid, (loc, val) in TOKENS.items():
        _w(fake, f"{smbios.TOKENS_DIR}/{tid:04x}_location", f"{loc:08x}\n")
        _w(fake, f"{smbios.TOKENS_DIR}/{tid:04x}_value", f"{val:08x}\n")
    _w(fake, smbios.DEVICE, "")
    fw = FakeFirmware()
    monkeypatch.setattr(smbios, "call", fw)
    return fw


def test_ioctl_number():
    # _IOWR('W', 0, struct dell_wmi_smbios_buffer) from <linux/wmi.h>
    assert smbios.DELL_WMI_SMBIOS_CMD == 0xC0345700


def test_smbios_battery_read(alienware_bat):
    (bat,) = laptop.batteries()
    assert bat.backend == "smbios"
    assert bat.charge_types == ["Standard", "Adaptive", "Fast", "Custom", "Long_Life"]
    assert bat.charge_type == "Adaptive"
    assert (bat.start_threshold, bat.end_threshold) == (50, 100)


def test_smbios_set_mode(alienware_bat):
    laptop.set_charge_type("express")
    assert laptop.batteries()[0].charge_type == "Fast"
    with pytest.raises(ValueError):
        laptop.set_charge_type("Turbo")


def test_smbios_thresholds(alienware_bat):
    with pytest.raises(ValueError):
        laptop.set_charge_thresholds(40, 80)
    laptop.set_charge_thresholds(60, 80)
    bat = laptop.batteries()[0]
    assert (bat.start_threshold, bat.end_threshold, bat.charge_type) == (60, 80, "Custom")
    # Moving the window up past the old end needs end written first.
    laptop.set_charge_thresholds(85, 95)
    assert alienware_bat.mem[0x20] == 85 and alienware_bat.mem[0x21] == 95


def test_smbios_not_root(alienware_bat, monkeypatch, capsys):
    def denied(*_a):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(smbios, "call", denied)
    (bat,) = laptop.batteries()
    assert bat.charge_type is None and "sudo" in bat.note
    assert cli.main(["battery"]) == 0
    assert "via dell-smbios" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Headset battery: Airoha RACE over HID
# --------------------------------------------------------------------------

from xeno_control.protocols import airoha_race  # noqa: E402


def test_race_request_bytes():
    req = airoha_race.build_request(airoha_race.OP_BATTERY, b"\x00")
    assert len(req) == 62
    assert req[:10] == bytes.fromhex("06 07 80 05 5a 03 00 d6 0c 00")
    assert set(req[10:]) == {0}


def test_race_parse_real_capture():
    # Report captured from the AC16250's headset dongle (battery 58%).
    report = bytes.fromhex("07 10 80 05 5b 03 00 d6 0c 00 05 5d 05 00 d6 0c 00 00 3a").ljust(62, b"\0")
    frames = airoha_race.parse_frames(airoha_race.report_payload(report))
    assert [(f.type, f.opcode) for f in frames] == [(0x5B, 0x0CD6), (0x5D, 0x0CD6)]
    assert frames[1].payload[2] == 58


class FakeDongle:
    def __init__(self, node, stale=b"", reply_after=2, level=58):
        self.reads, self.sent = 0, []
        self.stale = bytes([7, len(stale), 0]) + stale
        self.level, self.reply_after = level, reply_after

    def write(self, data):
        self.sent.append(data)

    def get_input(self):
        self.reads += 1
        if self.sent and self.reads > self.reply_after:
            race = bytes.fromhex("05 5d 05 00 d6 0c 00 00") + bytes([self.level])
            return (bytes([7, len(race), 0x80]) + race).ljust(62, b"\0")
        return self.stale.ljust(62, b"\0")

    def close(self):
        pass


def test_race_read_battery_skips_stale_snapshot():
    # A stale indication from an earlier poll must not be taken as the answer.
    old = bytes.fromhex("05 5d 05 00 d6 0c 00 00 63")  # 99%
    dongle = FakeDongle("x", stale=old, level=58)
    level = airoha_race.read_battery("x", transport=lambda n: dongle, sleep=lambda s: None)
    assert level == 58
    assert len(dongle.sent) == 1 and dongle.sent[0][7:9] == b"\xd6\x0c"


def test_race_read_battery_no_answer():
    dongle = FakeDongle("x", reply_after=10**9)
    assert airoha_race.read_battery("x", timeout=0.05, transport=lambda n: dongle,
                                    sleep=lambda s: None) is None
