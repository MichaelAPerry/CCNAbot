"""Mastery is a fold over evidence. These tests pin the pedagogy."""

import pytest

from ccnabot.domain.events import EventKind, Observation, Outcome, merge
from ccnabot.domain.mastery import (
    DEFAULT_POLICY, LearnerRecord, MasteryPolicy, cohort_coverage, fold_task,
)


# -- event hygiene -------------------------------------------------------

def test_naive_datetimes_are_rejected(at):
    from datetime import datetime
    with pytest.raises(ValueError, match="timezone-aware"):
        Observation("ana", "504", EventKind.DRILL, Outcome.CORRECT,
                    occurred_at=datetime(2026, 9, 22))


def test_attestation_outcomes_are_constrained(obs):
    with pytest.raises(ValueError, match="attestation"):
        obs(kind=EventKind.ATTESTATION, outcome=Outcome.CORRECT)
    with pytest.raises(ValueError, match="only attestation"):
        obs(kind=EventKind.DRILL, outcome=Outcome.ATTESTED)


def test_merge_is_idempotent_and_order_independent(obs, at):
    a = [obs(when=at(2026, 9, 1)), obs(when=at(2026, 9, 2))]
    b = [a[1], obs(when=at(2026, 9, 3))]
    assert [o.event_id for o in merge(a, b)] == [o.event_id for o in merge(b, a)]
    assert len(merge(a, b, a, b)) == 3


# -- the proficiency bar -------------------------------------------------

def test_one_lucky_answer_is_not_mastery(obs, at):
    m = fold_task("ana", "504", [obs(when=at(2026, 9, 1))])
    assert m.successes == 1
    assert not m.proficient


def test_massed_practice_in_one_sitting_is_not_mastery(obs, at):
    """Three correct answers four minutes apart is recognition, not retention.
    The distinct-day requirement is what makes this fail."""
    same_day = [obs(when=at(2026, 9, 1, 14, mm)) for mm in (0, 4, 9)]
    m = fold_task("ana", "504", same_day)
    assert m.successes == 3 and m.distinct_days == 1
    assert not m.proficient


def test_spaced_success_is_mastery(obs, at):
    spaced = [obs(when=at(2026, 9, d)) for d in (1, 8, 20)]
    m = fold_task("ana", "504", spaced)
    assert m.distinct_days == 3
    assert m.proficient


def test_successes_buried_in_failures_do_not_count(obs, at):
    events = [obs(when=at(2026, 9, d), outcome=Outcome.INCORRECT) for d in range(1, 12)]
    events += [obs(when=at(2026, 9, d)) for d in (13, 14, 15)]
    m = fold_task("ana", "504", events)
    assert m.successes == 3 and m.distinct_days == 3
    assert m.success_rate < DEFAULT_POLICY.min_success_rate
    assert not m.proficient


def test_policy_is_tunable(obs, at):
    lenient = MasteryPolicy(min_successes=1, min_distinct_days=1, min_success_rate=0.0)
    assert fold_task("ana", "504", [obs(when=at(2026, 9, 1))], lenient).proficient


@pytest.mark.parametrize("bad", [
    {"min_successes": 0}, {"min_distinct_days": 0}, {"min_success_rate": 1.5},
])
def test_invalid_policies_are_rejected(bad):
    with pytest.raises(ValueError):
        MasteryPolicy(**bad)


# -- attestation ---------------------------------------------------------

def test_attestation_alone_confers_proficiency(obs, at):
    """For ~23 physical tasks it is the only possible evidence."""
    m = fold_task("ana", "604", [obs(task="604", kind=EventKind.ATTESTATION,
                                     outcome=Outcome.ATTESTED, when=at(2026, 10, 1),
                                     instructor="mperry")])
    assert m.proficient and m.attested and m.attested_by == "mperry"
    assert m.attempts == 0, "an attestation is not an attempt"


def test_revocation_withdraws_it(obs, at):
    events = [
        obs(task="604", kind=EventKind.ATTESTATION, outcome=Outcome.ATTESTED,
            when=at(2026, 10, 1), instructor="mperry"),
        obs(task="604", kind=EventKind.ATTESTATION, outcome=Outcome.REVOKED,
            when=at(2026, 10, 9), instructor="mperry"),
    ]
    m = fold_task("ana", "604", events)
    assert not m.attested and not m.proficient


def test_latest_attestation_wins_regardless_of_insertion_order(obs, at):
    events = [
        obs(task="604", kind=EventKind.ATTESTATION, outcome=Outcome.REVOKED,
            when=at(2026, 10, 9)),
        obs(task="604", kind=EventKind.ATTESTATION, outcome=Outcome.ATTESTED,
            when=at(2026, 10, 1)),
    ]
    assert not fold_task("ana", "604", events).proficient
    assert not fold_task("ana", "604", list(reversed(events))).proficient


def test_proficiency_survives_later_failure(obs, at):
    """The (X) records that a competency was demonstrated. Forgetting is the
    retention scheduler's business, not the state record's."""
    events = [obs(when=at(2026, 9, d)) for d in (1, 8, 20)]
    events.append(obs(when=at(2027, 3, 1), outcome=Outcome.INCORRECT))
    m = fold_task("ana", "504", events)
    assert m.proficient, "earned proficiency is not revoked by a later slip"


# -- records -------------------------------------------------------------

def test_record_covers_the_whole_grid(catalog, obs, at):
    rec = LearnerRecord.build("ana", [obs(when=at(2026, 9, d)) for d in (1, 8, 20)], catalog)
    assert len(rec.tasks) == len(catalog) == 91
    assert rec.proficient_ids == ["504"]
    assert len(rec.untouched_ids) == 90
    assert len(rec.outstanding(catalog)) == 90
    assert rec.coverage() == pytest.approx(1 / 91)


def test_record_rejects_unknown_tasks(catalog, obs):
    with pytest.raises(ValueError, match="unknown task"):
        LearnerRecord.build("ana", [obs(task="805")], catalog)


def test_record_ignores_other_students(catalog, obs, at):
    events = [obs(student="bo", when=at(2026, 9, d)) for d in (1, 8, 20)]
    assert LearnerRecord.build("ana", events, catalog).proficient_ids == []


def test_cohort_coverage(catalog, obs, at):
    strong = LearnerRecord.build("ana", [obs(when=at(2026, 9, d)) for d in (1, 8, 20)], catalog)
    weak = LearnerRecord.build("bo", [], catalog)
    assert cohort_coverage([strong, weak], "504") == 0.5
    assert cohort_coverage([], "504") == 0.0
