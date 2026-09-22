"""Read-only console access to lab devices.

The assistant reads student gear. It must never write to it. Not "should
not" -- a bug or a cleverly-worded student request that reconfigured six
devices mid-lab would destroy trust in the tool permanently, and on shared
equipment it would wreck someone else's work too.

So the restriction is enforced here, at the only point that talks to a
device, rather than left to callers to respect:

* an allowlist of command verbs, not a blocklist of dangerous ones -- a
  blocklist is a promise to have thought of everything;
* a refusal to send anything containing a command separator, so an allowed
  verb cannot carry a second command along with it;
* no path to configuration mode at all.

If a device is already sitting in config mode (a previous student walked
away), commands are issued with `do` rather than exiting that mode, so the
assistant does not alter the state someone left behind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from .inventory import Device
from .ports import PortState, probe

# Only these verbs may ever be sent. `terminal length` is a session-local
# display setting, not configuration, and is needed to suppress paging.
ALLOWED = (
    "show",
    "terminal length",
    "terminal width",
)

# Characters that could smuggle a second command past the verb check.
SEPARATORS = re.compile(r"[;\r\n\x00]")

CONSOLE_DEFAULTS = {"baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1}


class ReadOnlyViolation(RuntimeError):
    """Raised when something tried to send a command that could change state."""


class ConsoleBusy(RuntimeError):
    """Raised when the port is in use -- almost always by a student."""


def validate(command: str) -> str:
    """Return the command if it is safe to send, else raise.

    Public and pure, so the rule is testable on its own and callers can check
    a command without opening a port.
    """
    if not command or not command.strip():
        raise ReadOnlyViolation("empty command")
    if SEPARATORS.search(command):
        raise ReadOnlyViolation(f"command contains a separator: {command!r}")

    normalised = " ".join(command.strip().lower().split())
    stripped = normalised[3:].lstrip() if normalised.startswith("do ") else normalised

    if not any(stripped == verb or stripped.startswith(verb + " ") for verb in ALLOWED):
        raise ReadOnlyViolation(
            f"refused: {command!r} is not a read-only command "
            f"(allowed: {', '.join(ALLOWED)})"
        )
    return command.strip()


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Who answered on the console."""

    hostname: str
    prompt: str
    in_config_mode: bool
    privileged: bool

    @property
    def is_usable(self) -> bool:
        return bool(self.hostname)


def _parse_prompt(prompt: str) -> DeviceIdentity:
    raw = prompt.strip()
    in_config = "(config" in raw
    privileged = raw.endswith("#")
    hostname = re.sub(r"\(config[^)]*\)", "", raw).rstrip("#>").strip()
    return DeviceIdentity(hostname, raw, in_config, privileged)


class ReadOnlyConsole:
    """A read-only netmiko session over a serial console.

    Usage::

        with ReadOnlyConsole(device, "/dev/ccnabot/rack3/sw1") as console:
            vlans = console.send("show vlan brief")
    """

    def __init__(
        self,
        device: Device,
        path: str,
        timeout: int = 20,
        check_port: bool = True,
        **serial_settings: Any,
    ) -> None:
        self.device = device
        self.path = str(path)
        self.timeout = timeout
        self.check_port = check_port
        self.serial_settings = {**CONSOLE_DEFAULTS, **serial_settings}
        self._conn = None
        self._identity: DeviceIdentity | None = None

    # -- lifecycle -------------------------------------------------------

    def connect(self) -> "ReadOnlyConsole":
        if self.check_port:
            state = probe(self.path)
            if state.state is PortState.MISSING:
                raise ConsoleBusy(f"{self.device.name}: {state.reason}")
            if not state.is_safe_to_open:
                raise ConsoleBusy(f"{self.device.name}: {state.reason}")

        try:
            from netmiko import ConnectHandler
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise RuntimeError(
                "netmiko is required for console access: pip install 'ccnabot[rack]'"
            ) from exc

        self._conn = ConnectHandler(
            device_type=self.device.netmiko_type,
            serial_settings={"port": self.path, **self.serial_settings},
            timeout=self.timeout,
        )
        self._identity = _parse_prompt(self._conn.find_prompt())
        return self

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.disconnect()
            finally:
                self._conn = None

    def __enter__(self) -> "ReadOnlyConsole":
        return self.connect()

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()

    # -- reading ---------------------------------------------------------

    @property
    def identity(self) -> DeviceIdentity | None:
        return self._identity

    def send(self, command: str, parse: bool = False) -> str | list[dict[str, Any]]:
        """Send one read-only command and return its output.

        `parse=True` returns structured data via ntc-templates where a
        template exists, which is what the checkpoint rules consume: matching
        on parsed fields rather than scraping CLI text.
        """
        command = validate(command)
        if self._conn is None:
            raise RuntimeError("not connected; use as a context manager")

        # Leave a config-mode session exactly as it was found.
        if self._identity and self._identity.in_config_mode and not command.lower().startswith("do "):
            command = f"do {command}"

        return self._conn.send_command(
            command, use_textfsm=parse, read_timeout=self.timeout
        )

    def identify(self) -> DeviceIdentity | None:
        """Re-read the prompt. Cheap, and the cable may have been moved."""
        if self._conn is not None:
            self._identity = _parse_prompt(self._conn.find_prompt())
        return self._identity
