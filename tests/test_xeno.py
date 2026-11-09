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
