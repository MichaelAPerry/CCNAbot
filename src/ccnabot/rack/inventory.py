"""What is in a rack, and where its consoles are.

Each rack holds the same six devices from the CertificationKits kit. With a
console cable per device they are all permanently attached to the rack PC,
so the assistant can reach any device the student is not currently using.

Console device nodes are addressed through stable udev symlinks
(/dev/ccnabot/<rack>/<device>) rather than /dev/ttyUSB*, because USB
enumeration order is not stable across reboots. Getting that wrong would have
the assistant read SW2 and report on it as though it were R1.
"""

from __future__ import annotations

import enum
import pathlib
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping

import yaml

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
TEMPLATE = DATA_DIR / "rack_template.yaml"
DEFAULT_SYMLINK_ROOT = pathlib.Path("/dev/ccnabot")


class Role(enum.Enum):
    ROUTER = "router"
    SWITCH_L2 = "switch_l2"
    SWITCH_L3 = "switch_l3"

    @property
    def is_switch(self) -> bool:
        return self in (Role.SWITCH_L2, Role.SWITCH_L3)

    @property
    def routes(self) -> bool:
        return self in (Role.ROUTER, Role.SWITCH_L3)


@dataclass(frozen=True, slots=True)
class Device:
    """One console-attached device."""

    name: str
    role: Role
    model: str
    platform: str = "cisco_ios"
    serial: str | None = None      # FTDI adapter serial, for the udev rule
    notes: str = ""

    @property
    def netmiko_type(self) -> str:
        """Netmiko needs the _serial variant for a console connection."""
        return f"{self.platform}_serial"

    def console(self, rack: str, root: pathlib.Path = DEFAULT_SYMLINK_ROOT) -> pathlib.Path:
        return root / rack / self.name.lower()

    def __str__(self) -> str:
        return f"{self.name} ({self.model})"


@dataclass(frozen=True, slots=True)
class Rack:
    """One bench: six devices and the PC that consoles them."""

    id: str
    devices: tuple[Device, ...]
    symlink_root: pathlib.Path = DEFAULT_SYMLINK_ROOT

    def __iter__(self) -> Iterator[Device]:
        return iter(self.devices)

    def __len__(self) -> int:
        return len(self.devices)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.upper() in {d.name for d in self.devices}

    def __getitem__(self, name: str) -> Device:
        for d in self.devices:
            if d.name.upper() == name.upper():
                return d
        raise KeyError(f"no device {name!r} in rack {self.id}")

    def get(self, name: str) -> Device | None:
        try:
            return self[name]
        except KeyError:
            return None

    def console(self, name: str) -> pathlib.Path:
        return self[name].console(self.id, self.symlink_root)

    def with_role(self, role: Role) -> list[Device]:
        return [d for d in self.devices if d.role is role]

    @property
    def routers(self) -> list[Device]:
        return [d for d in self.devices if d.role is Role.ROUTER]

    @property
    def switches(self) -> list[Device]:
        return [d for d in self.devices if d.role.is_switch]

    @property
    def cabled(self) -> list[Device]:
        """Devices whose console adapter has been registered."""
        return [d for d in self.devices if d.serial]

    @property
    def uncabled(self) -> list[Device]:
        return [d for d in self.devices if not d.serial]


def load_template(path: pathlib.Path | None = None) -> list[Device]:
    raw = yaml.safe_load((path or TEMPLATE).read_text(encoding="utf-8"))
    return [
        Device(
            name=d["name"],
            role=Role(d["role"]),
            model=d["model"],
            platform=d.get("platform", "cisco_ios"),
            serial=d.get("serial"),
            notes=d.get("notes", ""),
        )
        for d in raw["devices"]
    ]


def build_rack(
    rack_id: str,
    serials: Mapping[str, str] | None = None,
    template: Iterable[Device] | None = None,
    symlink_root: pathlib.Path = DEFAULT_SYMLINK_ROOT,
) -> Rack:
    """Build a rack from the standard kit, binding console adapter serials.

    `serials` maps device name to FTDI adapter serial, e.g. {"R1": "AB0KXYZ1"}.
    Devices without one are simply uncabled; the assistant skips them rather
    than failing, so a rack part-way through being wired still works.
    """
    serials = {k.upper(): v for k, v in (serials or {}).items()}
    devices = tuple(
        Device(
            name=d.name, role=d.role, model=d.model, platform=d.platform,
            serial=serials.get(d.name.upper()), notes=d.notes,
        )
        for d in (template or load_template())
    )
    unknown = set(serials) - {d.name.upper() for d in devices}
    if unknown:
        raise ValueError(f"serials given for devices not in the kit: {sorted(unknown)}")
    return Rack(id=rack_id, devices=devices, symlink_root=symlink_root)


def load_rack(path: pathlib.Path) -> Rack:
    """Load a rack PC's local config (rack id plus its adapter serials)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return build_rack(
        rack_id=str(raw["rack"]),
        serials=raw.get("consoles") or {},
        symlink_root=pathlib.Path(raw.get("symlink_root", DEFAULT_SYMLINK_ROOT)),
    )
