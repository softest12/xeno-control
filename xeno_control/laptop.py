"""Laptop backend: thermal/power profiles, fans, temperatures and battery.

Everything here uses mainline kernel interfaces, no reverse-engineered WMI calls:

* ``platform_profile`` (ACPI platform profile, provided on Alienware by the
  ``alienware-wmi`` / ``alienware-wmi-wmax`` driver). On models with G-Mode the
  driver maps G-Mode to the ``performance`` profile.
* ``hwmon`` (fans and temperatures; the Alienware driver registers as
  ``alienware_wmi`` on kernel 6.15+).
* ``power_supply`` (battery charge mode / thresholds, provided by
  ``dell-laptop`` on recent kernels). ``dell-laptop`` refuses to load on
  machines whose DMI vendor is "Alienware", so there we fall back to the
  ``dell-smbios`` WMI interface (see :mod:`xeno_control.smbios`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import smbios, sysfs

PROFILE_LABELS = {
    "low-power": "Economia",
    "cool": "Frio",
    "quiet": "Silencioso",
    "balanced": "Balanceado",
    "balanced-performance": "Desempenho balanceado",
    "performance": "Desempenho (G-Mode)",
    "custom": "Personalizado (ventoinhas manuais)",
}

# hwmon drivers that are interesting on an Alienware laptop.
HWMON_DRIVERS = ("alienware_wmi", "dell_smm", "coretemp", "k10temp", "nvme",
                 "acpitz", "amdgpu", "nouveau", "nvidia")

STATE_FILE = "/var/lib/xeno-control/state.json"


# --------------------------------------------------------------------------
# System info
# --------------------------------------------------------------------------

@dataclass
class SystemInfo:
    vendor: str | None
    product: str | None
    sku: str | None
    bios: str | None
    alienware_wmi_loaded: bool
    module_params: dict[str, str] = field(default_factory=dict)


def system_info() -> SystemInfo:
    dmi = sysfs.path("/sys/class/dmi/id")
    module = sysfs.path("/sys/module/alienware_wmi")
    params: dict[str, str] = {}
    if (module / "parameters").is_dir():
        for p in sorted((module / "parameters").iterdir()):
            params[p.name] = sysfs.read(p, "?") or "?"
    return SystemInfo(
        vendor=sysfs.read(dmi / "sys_vendor"),
        product=sysfs.read(dmi / "product_name"),
        sku=sysfs.read(dmi / "product_sku"),
        bios=sysfs.read(dmi / "bios_version"),
        alienware_wmi_loaded=module.is_dir(),
        module_params=params,
    )


# --------------------------------------------------------------------------
# Platform profile
# --------------------------------------------------------------------------

def _legacy_profile_paths() -> tuple[Path, Path]:
    base = sysfs.path("/sys/firmware/acpi")
    return base / "platform_profile", base / "platform_profile_choices"


def _class_handlers() -> list[Path]:
    base = sysfs.path("/sys/class/platform-profile")
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if (p / "profile").exists())


def profile_supported() -> bool:
    return _legacy_profile_paths()[0].exists() or bool(_class_handlers())


def _profile_paths() -> tuple[Path, Path] | None:
    """Prefer the Alienware handler, then the aggregated ACPI file."""
    for handler in _class_handlers():
        name = sysfs.read(handler / "name", "") or ""
        if "alienware" in name:
            return handler / "profile", handler / "choices"
    legacy = _legacy_profile_paths()
    if legacy[0].exists():
        return legacy
    handlers = _class_handlers()
    if handlers:
        return handlers[0] / "profile", handlers[0] / "choices"
    return None


def profile_choices() -> list[str]:
    paths = _profile_paths()
    if not paths:
        return []
    return (sysfs.read(paths[1], "") or "").split()


def current_profile() -> str | None:
    paths = _profile_paths()
    return sysfs.read(paths[0]) if paths else None


def set_profile(name: str) -> None:
    paths = _profile_paths()
    if not paths:
        raise RuntimeError("Nenhum platform_profile encontrado (driver alienware-wmi carregado?)")
    choices = profile_choices()
    if name not in choices:
        raise ValueError(f"Perfil '{name}' inválido. Opções: {', '.join(choices)}")
    sysfs.write(paths[0], name)
    save_state(profile=name)


# --------------------------------------------------------------------------
# Persisted state (restored at boot by xeno-control-restore.service)
# --------------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(sysfs.path(STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(**values: object) -> None:
    state_path = sysfs.path(STATE_FILE)
    try:
        state = load_state()
        state.update(values)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass  # not root: state simply isn't persisted


def restore_state() -> list[str]:
    """Re-apply saved settings. Returns human-readable log lines."""
    log: list[str] = []
    state = load_state()
    if "profile" in state:
        try:
            set_profile(state["profile"])
            log.append(f"perfil restaurado: {state['profile']}")
        except (OSError, ValueError, RuntimeError) as exc:
            log.append(f"falha ao restaurar perfil: {exc}")
    return log


# --------------------------------------------------------------------------
# hwmon: fans and temperatures
# --------------------------------------------------------------------------

@dataclass
class Sensor:
    chip: str
    kind: str          # "fan" | "temp" | "boost" | "pwm"
    index: int
    label: str
    value: float | None
    unit: str
    path: Path
    writable: bool = False


_ATTR_RE = re.compile(r"^(fan|temp|pwm)(\d+)(?:_(input|boost))?$")


def hwmon_chips(all_chips: bool = False) -> list[tuple[str, Path]]:
    base = sysfs.path("/sys/class/hwmon")
    if not base.is_dir():
        return []
    chips = []
    for hw in sorted(base.iterdir()):
        name = sysfs.read(hw / "name", "") or ""
        if all_chips or name in HWMON_DRIVERS:
            chips.append((name, hw))
    return chips


def read_sensors(all_chips: bool = False) -> list[Sensor]:
    sensors: list[Sensor] = []
    for chip, hw in hwmon_chips(all_chips):
        for attr in sorted(hw.iterdir(), key=lambda p: p.name):
            m = _ATTR_RE.match(attr.name)
            if not m:
                continue
            kind, idx, suffix = m.group(1), int(m.group(2)), m.group(3)
            idx_label = sysfs.read(hw / f"{kind}{idx}_label")
            if kind == "temp" and suffix == "input":
                raw = sysfs.read_int(attr)
                sensors.append(Sensor(chip, "temp", idx, idx_label or f"temp{idx}",
                                      raw / 1000 if raw is not None else None, "°C", attr))
            elif kind == "fan" and suffix == "input":
                sensors.append(Sensor(chip, "fan", idx, idx_label or f"fan{idx}",
                                      sysfs.read_int(attr), "RPM", attr))
            elif kind == "fan" and suffix == "boost":
                # alienware-wmi-wmax manual fan control (writable in "custom" profile)
                sensors.append(Sensor(chip, "boost", idx,
                                      (sysfs.read(hw / f"fan{idx}_label") or f"fan{idx}") + " boost",
                                      sysfs.read_int(attr), "/255", attr, sysfs.writable(attr)))
            elif kind == "pwm" and suffix is None:
                sensors.append(Sensor(chip, "pwm", idx, f"pwm{idx}",
                                      sysfs.read_int(attr), "/255", attr, sysfs.writable(attr)))
    return sensors


def set_fan_boost(index: int, value: int) -> Path:
    """Set manual fan boost (0-255) on the Alienware hwmon chip."""
    if not 0 <= value <= 255:
        raise ValueError("boost deve estar entre 0 e 255")
    for chip, hw in hwmon_chips():
        if chip != "alienware_wmi":
            continue
        for attr in (hw / f"fan{index}_boost", hw / f"pwm{index}"):
            if attr.exists():
                sysfs.write(attr, value)
                return attr
    raise RuntimeError(
        f"Controle manual da ventoinha {index} não disponível. Precisa do kernel 6.15+ "
        "com alienware-wmi e, em geral, do perfil 'custom' ativo.")


# --------------------------------------------------------------------------
# Battery
# --------------------------------------------------------------------------

@dataclass
class Battery:
    name: str
    path: Path
    capacity: int | None
    status: str | None
    charge_types: list[str]
    charge_type: str | None
    start_threshold: int | None
    end_threshold: int | None
    backend: str = "sysfs"          # "sysfs" (dell-laptop) | "smbios" | "none"
    note: str | None = None


def _parse_bracket_list(text: str | None) -> tuple[list[str], str | None]:
    if not text:
        return [], None
    options, selected = [], None
    for tok in text.split():
        if tok.startswith("[") and tok.endswith("]"):
            tok = tok[1:-1]
            selected = tok
        options.append(tok)
    if selected is None and len(options) == 1:
        selected = options[0]
    return options, selected


def batteries() -> list[Battery]:
    base = sysfs.path("/sys/class/power_supply")
    if not base.is_dir():
        return []
    out: list[Battery] = []
    for ps in sorted(base.iterdir()):
        if sysfs.read(ps / "type") != "Battery":
            continue
        types, current = _parse_bracket_list(sysfs.read(ps / "charge_types"))
        if not types:  # older single-value attribute
            current = sysfs.read(ps / "charge_type")
        out.append(Battery(
            name=ps.name, path=ps,
            capacity=sysfs.read_int(ps / "capacity"),
            status=sysfs.read(ps / "status"),
            charge_types=types, charge_type=current,
            start_threshold=sysfs.read_int(ps / "charge_control_start_threshold"),
            end_threshold=sysfs.read_int(ps / "charge_control_end_threshold"),
        ))
        bat = out[-1]
        has_sysfs = bool(types) or bat.charge_type is not None or bat.end_threshold is not None
        if has_sysfs:
            continue
        if len(out) == 1 and smbios.available():
            _fill_from_smbios(bat)  # dell-smbios only controls the primary battery
        else:
            bat.backend = "none"
    return out


def _fill_from_smbios(bat: Battery) -> None:
    bat.backend = "smbios"
    bat.charge_types = smbios.supported_modes()
    try:
        bat.charge_type = smbios.charge_mode()
        bat.start_threshold, bat.end_threshold = smbios.thresholds()
    except PermissionError:
        bat.note = "modo e limites via dell-smbios: rode com sudo para ler"
    except (OSError, smbios.SmbiosError) as exc:
        bat.note = f"dell-smbios: {exc}"


def _battery(name: str | None) -> Battery:
    bats = batteries()
    if not bats:
        raise RuntimeError("Nenhuma bateria encontrada")
    if name is None:
        return bats[0]
    for b in bats:
        if b.name == name:
            return b
    raise RuntimeError(f"Bateria '{name}' não encontrada")


def set_charge_type(charge_type: str, battery: str | None = None) -> None:
    bat = _battery(battery)
    if bat.backend == "smbios":
        smbios.set_charge_mode(charge_type)
        return
    if bat.charge_types and charge_type not in bat.charge_types:
        raise ValueError(f"Modo inválido. Opções: {', '.join(bat.charge_types)}")
    attr = bat.path / ("charge_types" if bat.charge_types else "charge_type")
    if not attr.exists():
        raise RuntimeError("Este kernel não expõe modos de carga (precisa de dell-laptop recente)")
    sysfs.write(attr, charge_type)


def set_charge_thresholds(start: int, end: int, battery: str | None = None) -> None:
    if not (0 <= start < end <= 100):
        raise ValueError("Use 0 <= início < fim <= 100")
    bat = _battery(battery)
    if bat.backend == "smbios":
        smbios.set_thresholds(start, end)
        # Thresholds only matter in "Custom" mode on Dell machines.
        if smbios.charge_mode() != "Custom":
            smbios.set_charge_mode("Custom")
        return
    start_p = bat.path / "charge_control_start_threshold"
    end_p = bat.path / "charge_control_end_threshold"
    if not end_p.exists():
        raise RuntimeError("Este kernel não expõe limites de carga para a bateria")
    # Dell firmware may enforce constraints; write in an order that avoids
    # transient start > end.
    if bat.end_threshold is not None and start >= bat.end_threshold:
        sysfs.write(end_p, end)
        if start_p.exists():
            sysfs.write(start_p, start)
    else:
        if start_p.exists():
            sysfs.write(start_p, start)
        sysfs.write(end_p, end)
    # Thresholds only matter in "Custom" mode on Dell machines.
    if "Custom" in bat.charge_types and bat.charge_type != "Custom":
        sysfs.write(bat.path / "charge_types", "Custom")
