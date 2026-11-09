"""Command line interface: ``xeno``."""
from __future__ import annotations

import argparse
import sys
import time

from . import __version__, devices, hid, laptop


def _err(msg: str) -> int:
    print(f"erro: {msg}", file=sys.stderr)
    return 1


def _perm_hint() -> int:
    return _err("permissão negada - rode com sudo (ou use a GUI, que pede senha via pkexec)")


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status(_args: argparse.Namespace) -> int:
    info = laptop.system_info()
    print(f"Sistema : {info.vendor or '?'} {info.product or '?'} (SKU {info.sku or '?'}, BIOS {info.bios or '?'})")
    print(f"Driver  : alienware-wmi {'carregado' if info.alienware_wmi_loaded else 'NÃO carregado'}")
    for k, v in info.module_params.items():
        print(f"          {k}={v}")

    current = laptop.current_profile()
    if current:
        print(f"Perfil  : {current} - {laptop.PROFILE_LABELS.get(current, '')}")
    else:
        print("Perfil  : indisponível (veja README > Solução de problemas)")

    for s in laptop.read_sensors():
        if s.kind in ("fan", "temp") and s.value is not None:
            print(f"  {s.chip:<14} {s.label:<22} {s.value:>7.0f} {s.unit}")

    for b in laptop.batteries():
        mode = f", modo {b.charge_type}" if b.charge_type else ""
        lim = f", limite {b.start_threshold}-{b.end_threshold}%" if b.end_threshold else ""
        print(f"Bateria : {b.name} {b.capacity}% ({b.status}{mode}{lim})")

    for p in devices.peripherals():
        bat = f" - bateria {p.battery()}" if p.supports_battery else ""
        print(f"Periférico: {p.title} [{p.vid:04x}:{p.pid:04x}]{bat}")
    return 0


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------

def cmd_profile(args: argparse.Namespace) -> int:
    if args.action == "restore":
        for line in laptop.restore_state():
            print(line)
        return 0
    if args.action == "set":
        if not args.name:
            return _err("informe o perfil, ex: xeno profile set performance")
        try:
            laptop.set_profile(args.name)
        except PermissionError:
            return _perm_hint()
        except (ValueError, RuntimeError) as exc:
            return _err(str(exc))
        print(f"Perfil ativo: {args.name}")
        return 0
    choices = laptop.profile_choices()
    if not choices:
        return _err("platform_profile não encontrado. O módulo alienware-wmi está carregado?")
    current = laptop.current_profile()
    for c in choices:
        mark = "*" if c == current else " "
        print(f" {mark} {c:<22} {laptop.PROFILE_LABELS.get(c, '')}")
    return 0


# --------------------------------------------------------------------------
# sensors / fans
# --------------------------------------------------------------------------

def _print_sensors(all_chips: bool) -> None:
    for s in laptop.read_sensors(all_chips):
        val = "?" if s.value is None else f"{s.value:.0f}"
        rw = " (gravável)" if s.writable else ""
        print(f"{s.chip:<14} {s.kind:<5} {s.label:<24} {val:>7} {s.unit}{rw}")


def cmd_sensors(args: argparse.Namespace) -> int:
    if not args.watch:
        _print_sensors(args.all)
        return 0
    try:
        while True:
            print("\033[2J\033[H", end="")
            _print_sensors(args.all)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_fan(args: argparse.Namespace) -> int:
    try:
        attr = laptop.set_fan_boost(args.index, args.value)
    except PermissionError:
        return _perm_hint()
    except (ValueError, RuntimeError) as exc:
        return _err(str(exc))
    print(f"{attr} = {args.value}")
    return 0


# --------------------------------------------------------------------------
# battery
# --------------------------------------------------------------------------

def cmd_battery(args: argparse.Namespace) -> int:
    try:
        if args.action == "mode":
            laptop.set_charge_type(args.values[0], args.battery)
        elif args.action == "limit":
            laptop.set_charge_thresholds(int(args.values[0]), int(args.values[1]), args.battery)
    except PermissionError:
        return _perm_hint()
    except (IndexError, ValueError, RuntimeError, OSError) as exc:
        return _err(str(exc))
    for b in laptop.batteries():
        via = {"smbios": " (via dell-smbios)", "sysfs": ""}.get(b.backend, "")
        print(f"{b.name}: {b.capacity}% {b.status}{via}")
        if b.charge_types:
            print("  modos  : " + " ".join(f"[{t}]" if t == b.charge_type else t for t in b.charge_types))
        if b.end_threshold is not None:
            print(f"  limites: início {b.start_threshold}%  fim {b.end_threshold}%")
        if b.note:
            print(f"  nota   : {b.note}")
        if b.backend == "none":
            print("  nota   : sem controle de carga (dell-laptop não carregado e dell-smbios ausente)")
    return 0


# --------------------------------------------------------------------------
# peripherals / raw HID
# --------------------------------------------------------------------------

def cmd_devices(args: argparse.Namespace) -> int:
    devs = hid.enumerate_devices(None if args.all else hid.VENDOR_IDS)
    if not devs:
        print("Nenhum dispositivo hidraw Dell/Alienware encontrado.")
        return 0
    for p in devices.peripherals() if not args.all else []:
        status = p.info.status if p.info else "desconhecido - contribua! (docs/REVERSE_ENGINEERING.md)"
        print(f"\n{p.title}  [{p.vid:04x}:{p.pid:04x}]\n  status: {status}")
        if p.supports_battery:
            print(f"  bateria: {p.battery()}")
        for d in p.interfaces:
            _print_interface(d)
    if args.all:
        for d in devs:
            print(f"\n{d.name} [{d.id}]")
            _print_interface(d)
    return 0


def _print_interface(d: hid.HidrawDevice) -> None:
    cols = ", ".join(str(c) for c in d.descriptor.collections) or "?"
    print(f"  {d.node:<14} if={d.interface if d.interface is not None else '?'}  {cols}")
    for label, reports in (("in", d.descriptor.input_reports),
                           ("out", d.descriptor.output_reports),
                           ("feat", d.descriptor.feature_reports)):
        if reports:
            desc = " ".join(f"id{rid}:{size}B" for rid, size in sorted(reports.items()))
            print(f"      {label:<4} {desc}")


def cmd_hid(args: argparse.Namespace) -> int:
    try:
        if args.action == "monitor":
            targets = hid.resolve(args.target)
            nodes = [d.node for d in targets if not args.vendor_only or d.has_vendor_collection]
            if not nodes:
                return _err(f"nenhum hidraw para {args.target}")
            print(f"Escutando {', '.join(nodes)} (Ctrl+C para sair)...")
            try:
                for ts, node, data in hid.monitor(nodes, args.duration):
                    print(f"{ts:9.3f} {node:<14} [{len(data):2d}] {hid.hexdump(data)}", flush=True)
            except KeyboardInterrupt:
                pass
        elif args.action == "send":
            data = hid.parse_hex(args.data)
            node = hid.resolve(args.target)[0].node
            if args.feature:
                hid.send_feature(node, data)
            else:
                hid.send_output(node, data)
            print(f"enviado para {node}: {hid.hexdump(data)}")
        elif args.action == "get-feature":
            node = hid.resolve(args.target)[0].node
            data = hid.get_feature(node, int(args.data, 0), args.length)
            print(hid.hexdump(data))
    except PermissionError:
        return _err("sem acesso ao hidraw - instale data/70-xeno-control.rules ou use sudo")
    except (OSError, ValueError, IndexError) as exc:
        return _err(str(exc))
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    from .gui import main as gui_main
    return gui_main()


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xeno", description="Central de controle livre para hardware Alienware no Linux")
    p.add_argument("--version", action="version", version=f"xeno-control {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status", help="resumo do sistema").set_defaults(func=cmd_status)

    sp = sub.add_parser("profile", help="perfis de energia/térmicos (G-Mode etc.)")
    sp.add_argument("action", nargs="?", choices=["list", "set", "restore"], default="list")
    sp.add_argument("name", nargs="?")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("sensors", help="temperaturas e ventoinhas")
    sp.add_argument("-w", "--watch", action="store_true")
    sp.add_argument("-i", "--interval", type=float, default=1.0)
    sp.add_argument("-a", "--all", action="store_true", help="todos os chips hwmon")
    sp.set_defaults(func=cmd_sensors)

    sp = sub.add_parser("fan", help="boost manual da ventoinha (perfil 'custom')")
    sp.add_argument("index", type=int, help="número da ventoinha (1, 2, ...)")
    sp.add_argument("value", type=int, help="0-255")
    sp.set_defaults(func=cmd_fan)

    sp = sub.add_parser("battery", help="modo de carga e limites da bateria")
    sp.add_argument("action", nargs="?", choices=["show", "mode", "limit"], default="show")
    sp.add_argument("values", nargs="*", help="mode: <Standard|Adaptive|Fast|Custom|Long_Life>  limit: <início> <fim>")
    sp.add_argument("--battery", help="ex: BAT0")
    sp.set_defaults(func=cmd_battery)

    sp = sub.add_parser("devices", help="periféricos Alienware (mouse, headset)")
    sp.add_argument("-a", "--all", action="store_true", help="todos os hidraw, de qualquer fabricante")
    sp.set_defaults(func=cmd_devices)

    sp = sub.add_parser("hid", help="ferramentas HID cruas para engenharia reversa")
    sp.add_argument("action", choices=["monitor", "send", "get-feature"])
    sp.add_argument("target", help="/dev/hidrawN ou vid:pid (ex: 413c:a529)")
    sp.add_argument("data", nargs="?", default="", help="send: bytes hex (1º = report id); get-feature: report id")
    sp.add_argument("--feature", action="store_true", help="send: usar feature report")
    sp.add_argument("--length", type=int, default=64, help="get-feature: tamanho")
    sp.add_argument("--duration", type=float, help="monitor: segundos")
    sp.add_argument("--vendor-only", action="store_true", help="monitor: só interfaces vendor-defined")
    sp.set_defaults(func=cmd_hid)

    sub.add_parser("gui", help="abrir a interface gráfica").set_defaults(func=cmd_gui)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_status(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
