"""Rack layer: inventory, console safety, port arbitration, udev pinning."""

import fcntl
import os
import pathlib
import subprocess
import sys
import time

import pytest

from ccnabot.rack.inventory import Device, Role, build_rack, load_template
from ccnabot.rack.ports import PortState, probe, readable, survey
from ccnabot.rack.session import ReadOnlyViolation, validate
from ccnabot.rack.udev import Adapter, duplicate_serials, rules_for


# -- inventory -----------------------------------------------------------

def test_kit_matches_the_purchase_order():
    """Six devices per rack: 1941, 2x 2811, 3750v2, 2x 2960."""
    devices = load_template()
    assert len(devices) == 6
    models = sorted(d.model for d in devices)
    assert models == [
        "Cisco 1941", "Cisco 2811", "Cisco 2811",
        "Cisco 2960-24TT-L", "Cisco 2960-24TT-L", "Cisco 3750v2-24PS",
    ]


def test_console_paths_are_stable_symlinks():
    rack = build_rack("rack3", {"SW1": "AB0K1"})
    assert rack.console("SW1") == pathlib.Path("/dev/ccnabot/rack3/sw1")
    assert rack["SW1"].netmiko_type == "cisco_ios_serial"


def test_layer3_switch_is_distinguished():
    """The 3750 routes; the 2960s do not. Checkpoint rules depend on this."""
    rack = build_rack("rack3")
    assert rack["SW1"].role is Role.SWITCH_L3 and rack["SW1"].role.routes
    assert rack["SW2"].role is Role.SWITCH_L2 and not rack["SW2"].role.routes
    assert len(rack.routers) == 3 and len(rack.switches) == 3


def test_partially_wired_rack_is_usable():
    """A rack half-way through being cabled should work, not fail."""
    rack = build_rack("rack3", {"R1": "A", "SW1": "B"})
    assert [d.name for d in rack.cabled] == ["R1", "SW1"]
    assert len(rack.uncabled) == 4


def test_serial_for_unknown_device_is_rejected():
    with pytest.raises(ValueError, match="not in the kit"):
        build_rack("rack3", {"R9": "A"})


def test_unknown_device_lookup(catalog):
    rack = build_rack("rack3")
    assert rack.get("R9") is None
    assert "SW2" in rack and "R9" not in rack
    with pytest.raises(KeyError):
        rack["R9"]


# -- read-only enforcement -----------------------------------------------

@pytest.mark.parametrize("command", [
    "show running-config",
    "show vlan brief",
    "SHOW IP INTERFACE BRIEF",
    "  show   interfaces   trunk  ",
    "show run | include vlan",
    "do show running-config",
    "terminal length 0",
])
def test_read_only_commands_are_allowed(command):
    assert validate(command)


@pytest.mark.parametrize("command", [
    "configure terminal",
    "conf t",
    "write memory",
    "write erase",
    "copy running-config startup-config",
    "erase startup-config",
    "delete flash:vlan.dat",
    "reload",
    "clear counters",
    "no shutdown",
    "interface fa0/1",
    "do configure terminal",
    "do write erase",
    "showconfigure terminal",
    "terminal monitor",
    "",
    "   ",
])
def test_state_changing_commands_are_refused(command):
    """An allowlist of verbs, not a blocklist of hazards: a blocklist is a
    promise to have thought of every dangerous command."""
    with pytest.raises(ReadOnlyViolation):
        validate(command)


@pytest.mark.parametrize("command", [
    "show run; configure terminal",
    "show run\nconfigure terminal",
    "show run\rreload",
    "show run\x00reload",
])
def test_separators_cannot_smuggle_a_second_command(command):
    with pytest.raises(ReadOnlyViolation, match="separator"):
        validate(command)


def test_refusal_names_the_command():
    with pytest.raises(ReadOnlyViolation, match="reload"):
        validate("reload")


# -- port arbitration ----------------------------------------------------

@pytest.fixture
def fake_port(tmp_path):
    p = tmp_path / "ttyFAKE0"
    p.write_text("")
    return p


def test_missing_port_is_reported_not_raised(tmp_path):
    result = probe(tmp_path / "absent")
    assert result.state is PortState.MISSING
    assert "udev" in result.reason


def test_free_port_is_safe_to_open(fake_port):
    result = probe(fake_port)
    assert result.state is PortState.FREE
    assert result.is_safe_to_open
    assert {"lockfile", "flock", "proc"} <= result.checks_run


def test_port_held_by_another_process_is_busy(fake_port):
    """A student on screen/minicom must never have the assistant open the
    same line underneath them."""
    child = subprocess.Popen([
        sys.executable, "-c",
        f"import fcntl,os,time; fd=os.open({str(fake_port)!r}, os.O_RDWR);"
        f" fcntl.flock(fd, fcntl.LOCK_EX); time.sleep(10)",
    ])
    try:
        time.sleep(0.6)
        result = probe(fake_port)
        assert result.state is PortState.BUSY
        assert not result.is_safe_to_open
    finally:
        child.terminate()
        child.wait()
    time.sleep(0.2)
    assert probe(fake_port).state is PortState.FREE


def test_student_declaration_overrides_detection(tmp_path):
    """Running unprivileged, the assistant cannot see another user's open
    descriptors, so a student saying 'I'm on SW2' is the better evidence."""
    root = tmp_path / "dev"
    (root / "rack3").mkdir(parents=True)
    for name in ("r1", "sw2"):
        (root / "rack3" / name).write_text("")
    rack = build_rack("rack3", {"R1": "A", "SW2": "B"}, symlink_root=root)

    assert readable(rack) == ["R1", "SW2"]
    assert readable(rack, skip={"SW2"}) == ["R1"]
    assert survey(rack, skip={"sw2"})["SW2"].reason.startswith("student declared")


def test_survey_skips_uncabled_devices(tmp_path):
    rack = build_rack("rack3", {"R1": "A"}, symlink_root=tmp_path)
    assert set(survey(rack)) == {"R1"}


# -- udev pinning --------------------------------------------------------

def test_rules_pin_by_serial_not_enumeration_order():
    rack = build_rack("rack3", {"R1": "AB0KXYZ1", "SW1": "AB0KQRS9"})
    rules = rules_for(rack)
    assert 'ATTRS{serial}=="AB0KXYZ1"' in rules
    assert 'SYMLINK+="ccnabot/rack3/r1"' in rules
    assert "ttyUSB" not in rules, "pinning must not reference enumeration order"


def test_rules_cover_only_cabled_devices():
    rack = build_rack("rack3", {"R1": "A"})
    rules = rules_for(rack)
    assert rules.count("SYMLINK+=") == 1


def test_rules_for_unwired_rack_are_harmless():
    rules = rules_for(build_rack("rack3"))
    assert "SYMLINK+=" not in rules
    assert "no console adapters" in rules


def test_duplicate_serials_are_detected():
    """Counterfeit FTDI chips share one serial. Pinning is then impossible
    and the assistant would silently confuse two devices."""
    adapters = [
        Adapter("ttyUSB0", "A1", "0403", "6001"),
        Adapter("ttyUSB1", "A1", "0403", "6001"),
        Adapter("ttyUSB2", "B2", "0403", "6001"),
    ]
    assert duplicate_serials(adapters) == {"A1": ["ttyUSB0", "ttyUSB1"]}


def test_adapter_without_serial_is_not_pinnable():
    assert not Adapter("ttyUSB0", None, "0403", "6001").pinnable
    assert Adapter("ttyUSB0", "A1", "0403", "6001").pinnable
