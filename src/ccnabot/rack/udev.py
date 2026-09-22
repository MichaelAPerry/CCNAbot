"""Stable console device names.

USB enumeration order is not stable: /dev/ttyUSB0 may be R1 today and SW2
after the next reboot, depending on hub port order and probe timing. With six
adapters on a hub that is not a remote possibility, it is the normal case.

The consequence is not a crash but something worse -- the assistant would
read one device and confidently report on it as another, telling a student
their router config is wrong while looking at a switch.

So each adapter is pinned by its FTDI serial number to a fixed symlink:

    /dev/ccnabot/rack3/r1 -> /dev/ttyUSB4

This is also why the cables must be genuine FTDI. Counterfeit chips routinely
share one serial number across every unit, or carry none at all, which makes
pinning impossible.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

FTDI_VENDOR = "0403"
RULES_PATH = pathlib.Path("/etc/udev/rules.d/70-ccnabot.rules")
SYS_TTY = pathlib.Path("/sys/class/tty")


@dataclass(frozen=True, slots=True)
class Adapter:
    """A USB-serial adapter currently attached to this PC."""

    tty: str                 # ttyUSB0
    serial: str | None
    vendor: str | None
    product: str | None
    description: str = ""

    @property
    def node(self) -> pathlib.Path:
        return pathlib.Path("/dev") / self.tty

    @property
    def is_ftdi(self) -> bool:
        return self.vendor == FTDI_VENDOR

    @property
    def pinnable(self) -> bool:
        """Usable for a stable rule: needs a serial that is actually unique."""
        return bool(self.serial) and self.serial not in ("", "0", "None")

    def __str__(self) -> str:
        who = self.description or self.product or "unknown"
        return f"{self.tty:<10}{self.serial or '(no serial)':<20}{who}"


def _read(path: pathlib.Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def discover(sys_tty: pathlib.Path = SYS_TTY) -> list[Adapter]:
    """List attached USB-serial adapters, newest sysfs order.

    Run this once per rack with all six cables plugged in and labelled, to
    build the rack's console map.
    """
    found: list[Adapter] = []
    if not sys_tty.exists():
        return found
    for entry in sorted(sys_tty.iterdir()):
        if not entry.name.startswith("ttyUSB"):
            continue
        # Walk up from the tty to the USB device that carries the descriptors.
        base = (entry / "device").resolve()
        serial = vendor = product = desc = None
        for _ in range(4):
            if serial is None:
                serial = _read(base / "serial")
            if vendor is None:
                vendor = _read(base / "idVendor")
            if product is None:
                product = _read(base / "idProduct")
            if desc is None:
                desc = _read(base / "product")
            base = base.parent
        found.append(Adapter(entry.name, serial, vendor, product, desc or ""))
    return found


def duplicate_serials(adapters: list[Adapter]) -> dict[str, list[str]]:
    """Serials claimed by more than one adapter -- the counterfeit signature.

    Pinning cannot work for these, and the assistant would silently mix the
    devices up, so this is worth failing loudly on at install time.
    """
    seen: dict[str, list[str]] = {}
    for a in adapters:
        if a.pinnable:
            seen.setdefault(a.serial, []).append(a.tty)
    return {s: ttys for s, ttys in seen.items() if len(ttys) > 1}


def rules_for(rack, group: str = "dialout", mode: str = "0660") -> str:
    """Generate udev rules pinning each cabled device to a stable symlink."""
    lines = [
        "# CCNAbot console pinning - GENERATED, do not hand-edit.",
        f"# Regenerate with: ccnabot rack udev --rack {rack.id}",
        "#",
        "# Install:",
        f"#   sudo cp 70-ccnabot.rules {RULES_PATH}",
        "#   sudo udevadm control --reload && sudo udevadm trigger",
        "#",
        f"# The account running ccnabot must be in the '{group}' group.",
        "",
    ]
    if not rack.cabled:
        lines.append("# (no console adapters registered for this rack yet)")
        return "\n".join(lines) + "\n"

    for device in rack.cabled:
        link = f"ccnabot/{rack.id}/{device.name.lower()}"
        lines.append(f"# {device.name} - {device.model}")
        lines.append(
            f'SUBSYSTEM=="tty", SUBSYSTEMS=="usb", ATTRS{{idVendor}}=="{FTDI_VENDOR}", '
            f'ATTRS{{serial}}=="{device.serial}", '
            f'SYMLINK+="{link}", GROUP="{group}", MODE="{mode}"'
        )
    lines.append("")
    return "\n".join(lines)
