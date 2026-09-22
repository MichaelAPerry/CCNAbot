"""The store must be append-only and must merge cleanly across eight racks."""

import sqlite3

import pytest

from ccnabot.domain.events import EventKind, Outcome
from ccnabot.store.eventlog import EventLog


@pytest.fixture
def log():
    with EventLog(":memory:", source="rack3") as store:
        yield store


def test_append_and_read_back(log, at):
    log.record("ana", "504", EventKind.DRILL, Outcome.CORRECT,
               occurred_at=at(2026, 9, 22), prompt="network address?")
    (got,) = log.observations("ana")
    assert got.task_id == "504"
    assert got.source == "rack3"
    assert got.detail["prompt"] == "network address?"
    assert got.occurred_at == at(2026, 9, 22)


def test_updates_are_blocked_at_the_database(log):
    """Evidence behind a signed state record must not be editable after the
    fact; corrections are appended, never applied in place."""
    log.record("ana", "504", EventKind.DRILL, Outcome.CORRECT)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._conn.execute("UPDATE observations SET outcome = 'incorrect'")


def test_deletes_are_blocked_at_the_database(log):
    log.record("ana", "504", EventKind.DRILL, Outcome.CORRECT)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        log._conn.execute("DELETE FROM observations")


def test_reappending_the_same_event_is_a_noop(log, obs):
    event = obs()
    log.append(event)
    log.append(event)
    assert log.count() == 1


def test_merge_converges_in_both_directions(at):
    """Students move between racks; each rack records locally. Union on
    event_id has to be idempotent and order-independent or records diverge."""
    a = EventLog(":memory:", "rackA")
    b = EventLog(":memory:", "rackB")
    a.record("ana", "504", EventKind.DRILL, Outcome.CORRECT, occurred_at=at(2026, 9, 1))
    b.record("ana", "703", EventKind.RACK_CHECK, Outcome.INCORRECT, occurred_at=at(2026, 9, 2))

    a.merge_from(b)
    a.merge_from(b)      # idempotent
    b.merge_from(a)

    assert a.count() == b.count() == 2
    assert [o.event_id for o in a] == [o.event_id for o in b]


def test_merge_reports_only_genuinely_new_rows(at):
    a, b = EventLog(":memory:", "a"), EventLog(":memory:", "b")
    b.record("ana", "504", EventKind.DRILL, Outcome.CORRECT)
    assert a.merge_from(b) == 1
    assert a.merge_from(b) == 0


def test_filters(log, at):
    log.record("ana", "504", EventKind.DRILL, Outcome.CORRECT, occurred_at=at(2026, 9, 1))
    log.record("ana", "703", EventKind.RACK_CHECK, Outcome.CORRECT, occurred_at=at(2026, 9, 5))
    log.record("bo", "504", EventKind.DRILL, Outcome.CORRECT, occurred_at=at(2026, 9, 9))

    assert len(log.observations("ana")) == 2
    assert len(log.observations(task_id="504")) == 2
    assert len(log.observations("ana", "504")) == 1
    assert len(log.observations(since=at(2026, 9, 5))) == 2
    assert log.student_ids() == ["ana", "bo"]


def test_results_are_ordered_for_deterministic_folding(log, at):
    for day in (9, 1, 5):
        log.record("ana", "504", EventKind.DRILL, Outcome.CORRECT, occurred_at=at(2026, 9, day))
    times = [o.occurred_at for o in log.observations("ana")]
    assert times == sorted(times)


def test_roster_is_mutable(log):
    log.upsert_student("ana", "Ana R.", cohort="2027")
    log.upsert_student("ana", "Ana Rodriguez", cohort="2027")
    (row,) = log.students()
    assert row["display_name"] == "Ana Rodriguez"
    log.upsert_student("ana", "Ana Rodriguez", active=False)
    assert log.students() == []
    assert len(log.students(active_only=False)) == 1


def test_persists_to_disk(tmp_path, at):
    path = tmp_path / "rack3.db"
    with EventLog(path, "rack3") as store:
        store.record("ana", "504", EventKind.DRILL, Outcome.CORRECT, occurred_at=at(2026, 9, 1))
    with EventLog(path, "rack3") as reopened:
        assert reopened.count() == 1
