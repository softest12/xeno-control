"""Cross-platform HID probe (Windows/Linux/macOS) using the `hidapi` package.

Lists Dell/Alienware HID interfaces and optionally dumps their input reports.
Handy on Windows, where Alienware Command Center is running: watch which
reports the mouse/headset send (battery, button presses) while you use AWCC.

    pip install hidapi
    python tools/hid_probe.py                 # list
    python tools/hid_probe.py --listen 413c:a529 --seconds 30
"""
from __future__ import annotations

import argparse
import time

import hid  # type: ignore[import-not-found]

VENDORS = {0x413C, 0x187C}


def listing() -> list[dict]:
    return [d for d in hid.enumerate() if d["vendor_id"] in VENDORS]


def print_list() -> None:
    for d in listing():
        print(f"{d['vendor_id']:04x}:{d['product_id']:04x}  if={d['interface_number']:<2} "
              f"usage=0x{d['usage_page']:04x}:0x{d['usage']:02x}  "
              f"{d['manufacturer_string']!s} / {d['product_string']!s}")
        print(f"    {d['path'].decode(errors='replace')}")


def listen(target: str, seconds: float) -> None:
    vid, pid = (int(x, 16) for x in target.split(":"))
    handles = []
    for d in listing():
        if d["vendor_id"] == vid and d["product_id"] == pid:
            h = hid.device()
            try:
                h.open_path(d["path"])
                h.set_nonblocking(True)
                handles.append((d, h))
            except OSError as exc:
                print(f"não abriu usage 0x{d['usage_page']:04x}:0x{d['usage']:02x}: {exc}")
    print(f"escutando {len(handles)} interfaces por {seconds}s...")
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        for d, h in list(handles):
            try:
                data = h.read(256)
            except OSError:
                # Windows keeps mouse/keyboard collections exclusive; skip them.
                print(f"ignorando usage 0x{d['usage_page']:04x}:0x{d['usage']:02x} (sem leitura)")
                handles.remove((d, h))
                h.close()
                continue
            if data:
                print(f"{time.monotonic() - start:8.3f} usage=0x{d['usage_page']:04x}:0x{d['usage']:02x} "
                      f"[{len(data):2d}] {' '.join(f'{b:02x}' for b in data)}", flush=True)
        time.sleep(0.005)
    for _, h in handles:
        h.close()
    print("fim.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", metavar="VID:PID")
    ap.add_argument("--seconds", type=float, default=20)
    args = ap.parse_args()
    if args.listen:
        listen(args.listen, args.seconds)
    else:
        print_list()


if __name__ == "__main__":
    main()
