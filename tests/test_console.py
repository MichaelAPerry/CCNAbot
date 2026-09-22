"""The console wrapper and session registry.

The wrapper exists so the assistant knows exactly which device each student
is on, rather than inferring it from a system that cannot tell it.
"""

import os
import pathlib
import sqlite3

import pytest

from ccnabot.rack.console import (
    BAUD, ConsoleUnavailable, KNOWN_TERMINALS, TerminalProgram, find_terminal,
    preflight, transcript_path,
)
from ccnabot.rack.inventory import build_rack
from ccnabot.store.eventlog import EventLog


@pytest.fixture
def log():
    with EventLog(":memory:", source="rack3") as store:
        yield store


@pytest.fixture
def wired(tmp_path):
    """A rack with R1 and SW1 cabled and their device nodes present."""
    root = tmp_path / "dev"
    (root / "rack3").mkdir(parents=True)
    for name in ("r1", "sw1"):
        (root / "rack3" / name).write_text("")
    return build_rack("rack3", {"R1": "A1", "SW1": "B2"}, symlink_root=root)


# -- session registry ----------------------------------------------------

def test_open_session_marks_a_device_in_use(log):
    log.open_console_session("ana", "rack3", "SW1", pid=os.getpid())
    assert log.devices_in_use("rack3") == {"SW1"}
    assert log.devices_in_use("rack5") == set()


def test_closing_frees_the_device(log):
    sid = log.open_console_session("ana", "rack3", "SW1")
    log.close_console_session(sid)
    assert log.devices_in_use("rack3") == set()


def test_device_names_are_normalised(log):
    log.open_console_session("ana", "rack3", "sw1")
    assert log.devices_in_use("rack3") == {"SW1"}


def test_a_closed_session_cannot_be_reopened(log):
    sid = log.open_console_session("ana", "rack3", "SW1")
    log.close_console_session(sid)
    with pytest.raises(sqlite3.IntegrityError, match="only be closed"):
        log._conn.execute("UPDATE console_sessions SET ended_at = NULL")


def test_a_session_cannot_be_reattributed(log):
    """Who was at a bench is part of the record, not an editable field."""
    log.open_console_session("ana", "rack3", "SW1")
    with pytest.raises(sqlite3.IntegrityError, match="only be closed"):
        log._conn.execute("UPDATE console_sessions SET student_id = 'bo'")


def test_sessions_cannot_be_deleted(log):
    log.open_console_session("ana", "rack3", "SW1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._conn.execute("DELETE FROM console_sessions")


def test_stale_sessions_are_reaped(log):
    """A dropped SSH connection leaves the row open, and the assistant would
    avoid that device forever."""
    log.open_console_session("ana", "rack3", "SW1", pid=os.getpid())   # alive
    log.open_console_session("bo", "rack3", "R2", pid=0x7FFFFFFF)      # gone
    assert log.reap_stale_sessions("rack3") == 1
    assert log.devices_in_use("rack3") == {"SW1"}


def test_sessions_without_a_pid_are_left_alone(log):
    """Rows synced from another rack PC carry pids that mean nothing here."""
    log.open_console_session("ana", "rack3", "SW1", pid=None)
    assert log.reap_stale_sessions("rack3") == 0
    assert log.devices_in_use("rack3") == {"SW1"}


def test_history_is_retained(log):
    sid = log.open_console_session("ana", "rack3", "R1")
    log.close_console_session(sid)
    log.open_console_session("ana", "rack3", "SW1")
    history = log.console_history("ana")
    assert [r["device"] for r in history] == ["R1", "SW1"]
    assert history[0]["ended_at"] and not history[1]["ended_at"]


# -- preflight -----------------------------------------------------------

def test_unknown_device_lists_what_is_available(wired):
    with pytest.raises(ConsoleUnavailable, match="Try:") as exc:
        preflight(wired, "r9")
    assert "R1" in str(exc.value)


def test_uncabled_device_says_so(wired):
    with pytest.raises(ConsoleUnavailable, match="no console cable"):
        preflight(wired, "sw2")


def test_missing_node_suggests_the_likely_cause(tmp_path):
    rack = build_rack("rack3", {"R1": "A1"}, symlink_root=tmp_path / "dev")
    with pytest.raises(ConsoleUnavailable, match="udev"):
        preflight(rack, "r1")


def test_free_device_passes_preflight(wired):
    assert preflight(wired, "r1") == wired.console("R1")


def test_busy_device_names_the_student(wired, log, monkeypatch):
    import ccnabot.rack.console as mod
    from ccnabot.rack.ports import PortProbe, PortState

    monkeypatch.setattr(mod, "probe", lambda node: PortProbe(
        pathlib.Path(node), PortState.BUSY, reason="open by picocom(123)"))
    log.open_console_session("bo", "rack3", "R1")

    with pytest.raises(ConsoleUnavailable, match="bo") as exc:
        preflight(wired, "r1", log)
    assert "already in use" in str(exc.value)


# -- terminal program ----------------------------------------------------

def test_picocom_is_preferred_when_present(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/" + n)
    assert find_terminal().name == "picocom"


def test_falls_back_to_what_is_installed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/screen" if n == "screen" else None)
    assert find_terminal().name == "screen"


def test_missing_terminal_is_actionable(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    with pytest.raises(ConsoleUnavailable, match="apt install picocom"):
        find_terminal()


@pytest.mark.parametrize("name", [t.name for t in KNOWN_TERMINALS])
def test_every_terminal_sets_the_console_baud_rate(name):
    term = next(t for t in KNOWN_TERMINALS if t.name == name)
    argv = term.command(pathlib.Path("/dev/ccnabot/rack3/r1"), None)
    assert str(BAUD) in argv, f"{name} must open the line at {BAUD} baud"
    assert "/dev/ccnabot/rack3/r1" in argv


@pytest.mark.parametrize("name", [t.name for t in KNOWN_TERMINALS])
def test_transcript_is_passed_through(name, tmp_path):
    term = next(t for t in KNOWN_TERMINALS if t.name == name)
    argv = term.command(pathlib.Path("/dev/x"), tmp_path / "s.log")
    assert str(tmp_path / "s.log") in argv


def test_unknown_terminal_is_rejected():
    with pytest.raises(ConsoleUnavailable, match="unsupported"):
        TerminalProgram("kermit", "").command(pathlib.Path("/dev/x"), None)


def test_transcript_paths_are_per_rack_and_identify_the_session(tmp_path):
    path = transcript_path("rack3", "SW1", "ana", tmp_path)
    assert path.parent == tmp_path / "rack3"
    assert "ana" in path.name and "sw1" in path.name
