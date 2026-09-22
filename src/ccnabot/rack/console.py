"""The student's way onto a device console.

    $ console sw1

This replaces `screen /dev/ttyUSB3` as the habit students learn, and it is
worth one wrapper because of what it makes exact. The assistant needs to know
which device a student is working on so it can leave that line alone. Inferred
from the system, that is unreliable -- an unprivileged process cannot see
another user's open file descriptors, and `screen` does not always leave a
lock file. Recorded at the point the student attaches, it is simply known.

It also gives the session a name and a transcript for free, so the assistant
can later see the `% Invalid input detected` a student hit and worked past --
process evidence that device state alone never shows.

Nothing here writes to a device. The wrapper hands the student an ordinary
terminal program and stays out of the way.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from .inventory import Rack
from .ports import PortState, probe

BAUD = 9600
TRANSCRIPT_ROOT = pathlib.Path("/var/log/ccnabot/transcripts")


class ConsoleUnavailable(RuntimeError):
    """The device cannot be opened, with a reason fit to show a student."""


@dataclass(frozen=True, slots=True)
class TerminalProgram:
    """A terminal emulator we know how to drive."""

    name: str
    exit_hint: str

    def command(self, node: pathlib.Path, transcript: pathlib.Path | None) -> list[str]:
        if self.name == "picocom":
            argv = ["picocom", "--baud", str(BAUD), "--omap", "crcrlf"]
            if transcript:
                argv += ["--logfile", str(transcript)]
            return argv + [str(node)]
        if self.name == "screen":
            argv = ["screen"]
            if transcript:
                argv += ["-L", "-Logfile", str(transcript)]
            return argv + [str(node), str(BAUD)]
        if self.name == "minicom":
            argv = ["minicom", "-b", str(BAUD), "-D", str(node)]
            if transcript:
                argv += ["-C", str(transcript)]
            return argv
        raise ConsoleUnavailable(f"unsupported terminal program {self.name!r}")


# picocom first: it is the least surprising on a serial line, logs cleanly,
# and does not leave a detached session behind when a student closes SSH.
KNOWN_TERMINALS = (
    TerminalProgram("picocom", "Ctrl-A then Ctrl-X to quit"),
    TerminalProgram("screen", "Ctrl-A then K, then Y, to quit"),
    TerminalProgram("minicom", "Ctrl-A then X to quit"),
)


def find_terminal(preferred: str | None = None) -> TerminalProgram:
    candidates = KNOWN_TERMINALS
    if preferred:
        candidates = tuple(t for t in KNOWN_TERMINALS if t.name == preferred) or candidates
    for term in candidates:
        if shutil.which(term.name):
            return term
    raise ConsoleUnavailable(
        "no terminal program found. Install one: apt install picocom"
    )


def transcript_path(
    rack_id: str, device: str, student: str, root: pathlib.Path = TRANSCRIPT_ROOT
) -> pathlib.Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / rack_id / f"{stamp}-{student}-{device.lower()}.log"


def preflight(rack: Rack, device_name: str, log=None) -> pathlib.Path:
    """Check a device can be opened, raising a student-readable error if not.

    Separate from attach() so the CLI can explain the problem without having
    already recorded a session.
    """
    device = rack.get(device_name)
    if device is None:
        known = ", ".join(d.name for d in rack)
        raise ConsoleUnavailable(f"no device {device_name!r} in {rack.id}. Try: {known}")
    if not device.serial:
        raise ConsoleUnavailable(
            f"{device.name} has no console cable registered on this rack PC"
        )

    node = rack.console(device.name)
    state = probe(node)
    if state.state is PortState.MISSING:
        raise ConsoleUnavailable(
            f"{device.name}: no console device at {node}. "
            "The adapter may be unplugged, or the udev rules not installed."
        )
    if state.state is PortState.UNREADABLE:
        raise ConsoleUnavailable(
            f"{device.name}: cannot open {node}. "
            "Your account probably needs to be in the 'dialout' group."
        )
    if state.state is PortState.BUSY:
        who = ""
        if log is not None:
            who = next(
                (f" ({r['student_id']})" for r in log.open_sessions(rack.id)
                 if r["device"] == device.name.upper()),
                "",
            )
        raise ConsoleUnavailable(f"{device.name} is already in use{who}. {state.reason}")
    return node


def attach(
    rack: Rack,
    device_name: str,
    student: str,
    log,
    record_transcript: bool = True,
    terminal: str | None = None,
    transcript_root: pathlib.Path = TRANSCRIPT_ROOT,
) -> int:
    """Open a console session and hand the student their terminal.

    Blocks until they quit, then closes the session record. The record is
    closed in a finally block: a session left open would make the assistant
    avoid that device indefinitely.
    """
    node = preflight(rack, device_name, log)
    device = rack[device_name]
    term = find_terminal(terminal)

    transcript = None
    if record_transcript:
        transcript = transcript_path(rack.id, device.name, student, transcript_root)
        try:
            transcript.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            transcript = None  # logging is a bonus, never a blocker

    session_id = log.open_console_session(
        student_id=student, rack_id=rack.id, device=device.name,
        transcript=str(transcript) if transcript else None, pid=os.getpid(),
    )

    print(f"Connected to {device.name} ({device.model}) on {rack.id} at {BAUD} baud.")
    print(f"{term.exit_hint}. Press Enter if the screen is blank.")
    if transcript:
        print("This session is being recorded for your instructor.")
    print()

    try:
        return subprocess.call(term.command(node, transcript))
    except KeyboardInterrupt:
        return 130
    finally:
        log.close_console_session(session_id)
        print(f"\nDisconnected from {device.name}.")
