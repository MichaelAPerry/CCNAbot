"""Retention scheduling: the layer that keeps prior-year content alive."""

from datetime import date

import pytest

from ccnabot.domain.events import EventKind, Observation, Outcome
from ccnabot.scheduling.retention import (
    RetentionPolicy, RetentionScheduler, Urgency, replay,
)
from fsrs import Scheduler


def _fsrs() -> Scheduler:
    return Scheduler(enable_fuzzing=False)

TODAY = date(2026, 9, 22)


@pytest.fixture
def sched(catalog, calendar):
    return RetentionScheduler(catalog, calendar)


def reviews(task, days, at, outcome=Outcome.CORRECT, student="ana"):
    return [
        Observation(student, task, EventKind.RECALL_REVIEW, outcome,
                    occurred_at=at(2026, 8, d), source="rack3")
        for d in days
    ]


def test_replay_is_deterministic(at):
    """Card state is derived, never stored, so the same log must always
    rebuild the same card -- on any rack, in any order."""
    events = reviews("203", [1, 8, 20], at)
    c1, n1 = replay(events, _fsrs())
    c2, n2 = replay(list(reversed(events)), _fsrs())
    assert n1 == n2 == 3
    assert c1.due == c2.due and c1.stability == c2.stability


def test_fuzzing_must_stay_disabled(catalog, calendar, at):
    """FSRS fuzzes intervals randomly by default. With state derived from the
    log rather than stored, that would hand the same student different due
    dates on different racks."""
    sched = RetentionScheduler(catalog, calendar)
    assert sched._fsrs.enable_fuzzing is False
    dues = {sched.assess("203", reviews("203", [1, 8, 20], at), TODAY).due
            for _ in range(8)}
    assert len(dues) == 1, f"nondeterministic due dates: {dues}"


def test_unreviewed_prior_year_task_is_top_priority(sched, at):
    """Hardware was taught two years ago, is on NOCTI, and the plan never
    revisits it. Nothing is more worth asking about."""
    item = sched.assess("203", [], TODAY)
    assert item.urgency is Urgency.NEVER_SEEN
    assert item.never_retaught
    assert item.priority > 0


def test_untaught_in_year_content_is_held_back(sched, at):
    """Drilling students on material the class has not reached is not
    scaffolding, it is noise."""
    item = sched.assess("906", [], TODAY)   # troubleshooting, taught in T3
    assert item.urgency is Urgency.HELD
    assert not item.is_actionable
    assert item.priority == 0


def test_content_already_taught_is_fair_game(sched):
    item = sched.assess("501", [], TODAY)   # taught T1 W5-6
    assert item.urgency is Urgency.NEVER_SEEN
    assert item.is_actionable


def test_reviewing_raises_retrievability(sched, at):
    cold = sched.assess("203", [], TODAY)
    warm = sched.assess("203", reviews("203", [20], at), TODAY)
    assert warm.reviews == 1
    assert warm.retrievability > cold.retrievability


def test_never_retaught_outranks_equivalent_in_plan_task(sched, at):
    """Same evidence, different curricular fate: the one nothing will
    reinforce should come first."""
    prior_year = sched.assess("203", reviews("203", [20], at), TODAY)
    in_plan = sched.assess("501", reviews("501", [20], at), TODAY)
    assert prior_year.never_retaught and not in_plan.never_retaught
    assert prior_year.priority > in_plan.priority


def test_queue_is_bounded_and_ordered(sched, at):
    q = sched.queue(reviews("203", [20], at), today=TODAY, student_id="ana")
    assert 0 < len(q) <= RetentionPolicy().max_daily_items
    assert q == sorted(q, key=lambda i: -i.priority) or all(
        q[i].priority >= q[i + 1].priority for i in range(len(q) - 1)
    )


def test_queue_excludes_held_items(sched, at):
    q = sched.queue([], today=TODAY, limit=90, student_id="ana")
    assert all(i.is_actionable for i in q)
    assert "906" not in {i.task_id for i in q}


def test_queue_interleaves_topics(sched, at):
    """A queue that marches through the grid in task order gives blocked
    practice. Mixing topics is what makes retrieval effortful."""
    q = sched.queue([], today=TODAY, limit=10, student_id="ana")
    sections = [i.section for i in q]
    assert len(set(sections)) >= 4, f"expected a mix, got {sections}"
    runs = sum(1 for a, b in zip(sections, sections[1:]) if a == b)
    assert runs <= 2, f"too much blocking: {sections}"


def test_queue_is_stable_within_a_day_and_varies_between_days(sched):
    monday = [i.task_id for i in sched.queue([], today=TODAY, student_id="ana")]
    again = [i.task_id for i in sched.queue([], today=TODAY, student_id="ana")]
    tuesday = [i.task_id for i in sched.queue([], today=date(2026, 9, 23), student_id="ana")]
    assert monday == again, "a student's queue must not reshuffle mid-session"
    assert monday != tuesday


def test_queue_differs_between_students(sched):
    a = [i.task_id for i in sched.queue([], today=TODAY, student_id="ana")]
    b = [i.task_id for i in sched.queue([], today=TODAY, student_id="bo")]
    assert a != b


def test_exam_proximity_raises_priority(catalog, calendar, at):
    """The same neglected task should feel more urgent in March than in
    September."""
    sched = RetentionScheduler(catalog, calendar)
    autumn = sched.assess("203", reviews("203", [20], at), date(2026, 11, 1))
    spring = sched.assess("203", reviews("203", [20], at), date(2027, 4, 1))
    assert spring.priority > autumn.priority


def test_at_risk_is_unbounded(sched):
    assert len(sched.at_risk([], today=TODAY)) > RetentionPolicy().max_daily_items


def test_explicit_rating_overrides_outcome_mapping(sched, at):
    """A correct answer that took three minutes is not a 'Good' recall."""
    easy = [Observation("ana", "203", EventKind.DRILL, Outcome.CORRECT,
                        occurred_at=at(2026, 8, 20), detail={"rating": 4})]
    hard = [Observation("ana", "203", EventKind.DRILL, Outcome.CORRECT,
                        occurred_at=at(2026, 8, 20), detail={"rating": 2})]
    assert sched.assess("203", easy, TODAY).retrievability > \
           sched.assess("203", hard, TODAY).retrievability


@pytest.mark.parametrize("bad", [{"target_retention": 0.0}, {"max_daily_items": 0}])
def test_invalid_policy_rejected(bad):
    with pytest.raises(ValueError):
        RetentionPolicy(**bad)
