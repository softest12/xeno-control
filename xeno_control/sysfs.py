"""Tiny helpers around sysfs.

Every path goes through :func:`path`, which prefixes ``XENO_SYSFS_ROOT``
(default ``/``). Tests point that variable at a fake tree.
"""
from __future__ import annotations

import os
from pathlib import Path


def root() -> Path:
    return Path(os.environ.get("XENO_SYSFS_ROOT", "/"))


def path(p: str) -> Path:
    return root() / p.lstrip("/")


def read(p: Path, default: str | None = None) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def read_int(p: Path, default: int | None = None) -> int | None:
    value = read(p)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def write(p: Path, value: object) -> None:
    """Write a value to a sysfs attribute. Raises PermissionError when not root."""
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(str(value))


def writable(p: Path) -> bool:
    return p.exists() and os.access(p, os.W_OK)
