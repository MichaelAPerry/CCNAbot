"""Deciding whether a console port is safe for the assistant to open.

A serial line has one owner. If the assistant opens the port a student is
working on, both processes read the same bytes and write into the same
stream: the student's typing gets corrupted and the device echoes garbage
back at them. That is worse than the assistant simply staying quiet, so the
rule here is to skip on any doubt.

No single check is sufficient, because each has a blind spot:

* **UUCP lock files** (/var/lock/LCK..ttyUSB0) are what minicom and picocom
  create. They are world-readable, so this works across users -- but `screen`
  does not always create one.
* **flock** detects another process holding an advisory lock, but only if
  that process took one.
* **/proc/<pid>/fd** is the most direct evidence and catches every tool,
  but a process owned by another user is only visible to root or a process
  with CAP_DAC_READ_SEARCH. Running as a normal user it sees nothing.

So `probe()` gathers whatever evidence it can and reports FREE only when
every available check agrees. Anything else is treated as busy.
"""

from __future__ import annotations

import enum
import fcntl
import os
import pathlib
from dataclasses import dataclass, field

LOCK_DIRS = (pathlib.Path("/var/lock"), pathlib.Path("/run/lock"))


class PortState(enum.Enum):
    FREE = "free"            # every available check agrees nobody holds it
    BUSY = "busy"            # positive evidence of another holder
    MISSING = "missing"      # no device node -- not cabled, or udev not applied
    UNREADABLE = "unreadable"  # exists but we cannot open it (permissions)

    @property
    def is_safe_to_open(self) -> bool:
        return self is PortState.FREE


@dataclass(frozen=True, slots=True)
class PortProbe:
    """What we could establish about one console port."""

    path: pathlib.Path
    state: PortState
    node: pathlib.Path | None = None        # resolved /dev/ttyUSB*
    holders: tuple[tuple[int, str], ...] = ()   # (pid, command) where visible
    lock_file: pathlib.Path | None = None
    checks_run: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""

    @property
    def is_safe_to_open(self) -> bool:
        return self.state.is_safe_to_open

    def __str__(self) -> str:
        who = ", ".join(f"{c}({p})" for p, c in self.holders)
        return f"{self.path.name}: {self.state.value}" + (f" - {who}" if who else "")


def _lock_file_for(node: pathlib.Path) -> pathlib.Path | None:
    for d in LOCK_DIRS:
        candidate = d / f"LCK..{node.name}"
        if candidate.exists():
            return candidate
    return None


def _holders_via_proc(node: pathlib.Path) -> tuple[tuple[int, str], ...]:
    """Processes with the device open, as far as this user can see.

    Silently incomplete when the assistant does not run as root: other users'
    file descriptors are not readable. Absence of holders here is therefore
    weak evidence, which is why it is only one input to the decision.
    """
    found: list[tuple[int, str]] = []
    try:
        target = node.resolve()
    except OSError:
        return ()
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        try:
            for fd in fd_dir.iterdir():
                try:
                    if fd.resolve() == target:
                        comm = (entry / "comm").read_text().strip()
                        found.append((int(entry.name), comm))
                        break
                except OSError:
                    continue
        except (PermissionError, FileNotFoundError, OSError):
            continue
    return tuple(found)


def _flock_is_free(node: pathlib.Path) -> bool | None:
    """True if we could take an exclusive lock, None if we could not test."""
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def probe(path: str | pathlib.Path, use_proc: bool = True) -> PortProbe:
    """Establish whether `path` is safe for the assistant to open."""
    path = pathlib.Path(path)
    if not path.exists():
        return PortProbe(path, PortState.MISSING,
                         reason="no device node; adapter unplugged or udev rule not applied")

    node = path.resolve()
    checks: set[str] = set()

    lock = _lock_file_for(node)
    checks.add("lockfile")
    if lock is not None:
        return PortProbe(path, PortState.BUSY, node=node, lock_file=lock,
                         checks_run=frozenset(checks),
                         reason=f"UUCP lock held ({lock})")

    holders: tuple[tuple[int, str], ...] = ()
    if use_proc:
        checks.add("proc")
        holders = _holders_via_proc(node)
        mine = os.getpid()
        holders = tuple((p, c) for p, c in holders if p != mine)
        if holders:
            who = ", ".join(f"{c}({p})" for p, c in holders)
            return PortProbe(path, PortState.BUSY, node=node, holders=holders,
                             checks_run=frozenset(checks),
                             reason=f"open by {who}")

    checks.add("flock")
    free = _flock_is_free(node)
    if free is None:
        return PortProbe(path, PortState.UNREADABLE, node=node,
                         checks_run=frozenset(checks),
                         reason="cannot open the port (permissions or device error)")
    if not free:
        return PortProbe(path, PortState.BUSY, node=node,
                         checks_run=frozenset(checks),
                         reason="another process holds an advisory lock")

    return PortProbe(path, PortState.FREE, node=node, checks_run=frozenset(checks),
                     reason="no holder detected")


def survey(rack, skip: set[str] | None = None) -> dict[str, PortProbe]:
    """Probe every cabled device in a rack.

    `skip` names devices the student has declared they are working on. That
    declaration is deliberately trusted over the automatic checks: when the
    assistant runs as an unprivileged user it cannot see another user's open
    descriptors, so a student saying "I'm on SW2" is better evidence than
    anything probe() can gather.
    """
    skip = {s.upper() for s in (skip or set())}
    out: dict[str, PortProbe] = {}
    for device in rack.cabled:
        path = rack.console(device.name)
        if device.name.upper() in skip:
            out[device.name] = PortProbe(
                path, PortState.BUSY, checks_run=frozenset({"declared"}),
                reason="student declared they are working on this device",
            )
        else:
            out[device.name] = probe(path)
    return out


def readable(rack, skip: set[str] | None = None) -> list[str]:
    """Device names the assistant may safely open right now."""
    return [n for n, p in survey(rack, skip).items() if p.is_safe_to_open]
