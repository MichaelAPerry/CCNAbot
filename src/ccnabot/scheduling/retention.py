"""Retention scheduling: keeping prior-year competencies alive until NOCTI.

Both NOCTI exams assess the whole CIP 11.0901 grid, but the 2026-27 plan
reteaches only 32 of its 91 live tasks. The remaining 59 -- computer hardware,
OS administration, repair, most of the safety block -- were learned one or two
years ago and are not revisited before the exam. Left alone they decay, and the
class discovers this in April with two weeks to spare.

That is what this module is for. FSRS schedules each task's next review from
the student's own recall history, so the review load is spread across the year
instead of massed into exam week.

py-fsrs is kept behind this module deliberately: `Card`, `Rating` and
`Scheduler` never appear in the domain layer, so the scheduler could be
swapped without touching the learner model.
"""

from __future__ import annotations

import enum
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Mapping, Sequence

from fsrs import Card, Rating, Scheduler

from ..domain.calendar import AcademicCalendar, Exam
from ..domain.competency import Catalog, Modality
from ..domain.events import EventKind, Observation, Outcome

# How an observed outcome becomes an FSRS grade. A drill can override this by
# putting an explicit 1-4 in detail['rating'] -- e.g. to mark a correct answer
# that took three minutes as Hard rather than Good.
_OUTCOME_RATING = {
    Outcome.INCORRECT: Rating.Again,
    Outcome.PARTIAL: Rating.Hard,
    Outcome.CORRECT: Rating.Good,
}

# Observation kinds that count as retrieval practice.
_REVIEW_KINDS = (EventKind.RECALL_REVIEW, EventKind.DRILL)


class Urgency(enum.Enum):
    """Why a task is in the queue. Drives ordering and what the tutor says."""

    NEVER_SEEN = "never_seen"      # no recall evidence at all
    OVERDUE = "overdue"            # FSRS due date has passed
    DECAYING = "decaying"          # retrievability below target, not yet due
    SCHEDULED = "scheduled"        # due today
    HELD = "held"                  # not yet taught this year; do not drill


@dataclass(frozen=True, slots=True)
class ReviewItem:
    task_id: str
    section: str
    urgency: Urgency
    retrievability: float          # estimated P(recall) right now, 0..1
    due: date | None
    days_overdue: int
    reviews: int
    never_retaught: bool           # not scheduled anywhere in this year's plan
    priority: float

    @property
    def is_actionable(self) -> bool:
        return self.urgency is not Urgency.HELD


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Tuning for the review queue.

    `target_retention` is the recall probability below which a task is worth
    revisiting. 0.9 is the FSRS default and is a reasonable classroom setting:
    high enough that students are not constantly re-learning, low enough that
    reviews stay effortful, which is what makes them work.

    `never_retaught_weight` biases the queue toward the 59 tasks nothing else
    in the year will reinforce. Tasks the class is about to cover in lessons
    do not need the tutor's scarce time.
    """

    target_retention: float = 0.9
    never_retaught_weight: float = 1.6
    exam_horizon_days: int = 120
    max_daily_items: int = 12

    def __post_init__(self) -> None:
        if not 0.0 < self.target_retention < 1.0:
            raise ValueError("target_retention must be in (0, 1)")
        if self.max_daily_items < 1:
            raise ValueError("max_daily_items must be >= 1")


DEFAULT_RETENTION = RetentionPolicy()


def _rating(obs: Observation) -> Rating:
    explicit = obs.detail.get("rating")
    if explicit is not None:
        return Rating(int(explicit))
    return _OUTCOME_RATING[obs.outcome]


def replay(observations: Iterable[Observation], scheduler: Scheduler) -> tuple[Card, int]:
    """Rebuild FSRS card state by replaying a task's review history.

    Card state is derived, never stored: the observation log stays the single
    source of truth, and a card rebuilt on any rack PC from merged logs comes
    out identical.
    """
    reviews = sorted(
        (o for o in observations if o.kind in _REVIEW_KINDS),
        key=lambda o: o.sort_key,
    )
    card = Card()
    for obs in reviews:
        card, _ = scheduler.review_card(card, _rating(obs), review_datetime=obs.occurred_at)
    return card, len(reviews)


def _interleave(items: Sequence[ReviewItem], seed: str) -> list[ReviewItem]:
    """Spread equal-priority items across sections instead of running them in
    task-number order.

    Without this a queue of tied items marches through the grid numerically --
    eight safety questions, then eight hardware questions. Interleaving topics
    rather than blocking them is one of the better-evidenced retrieval-practice
    findings: mixing forces the student to *choose* an approach each time
    instead of applying the same one repeatedly.

    Only genuine ties are reordered, so priority ordering is preserved exactly.
    `seed` (student + date) rotates which section leads, so no section is
    permanently first, while the queue stays stable within a day.
    """
    out: list[ReviewItem] = []
    i = 0
    while i < len(items):
        j = i
        while j < len(items) and items[j].priority == items[i].priority:
            j += 1
        tied = items[i:j]
        if len(tied) > 2:
            buckets: dict[str, list[ReviewItem]] = {}
            for item in tied:
                buckets.setdefault(item.section, []).append(item)
            order = sorted(buckets)
            if order:
                # zlib.crc32, not hash(): str hashing is salted per process,
                # which would make the same day's queue differ across restarts.
                rot = zlib.crc32(seed.encode()) % len(order)
                order = order[rot:] + order[:rot]
            while any(buckets[s] for s in order):
                for s in order:
                    if buckets[s]:
                        out.append(buckets[s].pop(0))
        else:
            out.extend(tied)
        i = j
    return out


class RetentionScheduler:
    """Builds a student's review queue from their observation log."""

    def __init__(
        self,
        catalog: Catalog,
        calendar: AcademicCalendar,
        policy: RetentionPolicy = DEFAULT_RETENTION,
        scheduler: Scheduler | None = None,
    ) -> None:
        self.catalog = catalog
        self.calendar = calendar
        self.policy = policy
        # enable_fuzzing must stay off. FSRS fuzzes intervals by +/- a few
        # days to stop a personal collection from piling every card onto one
        # date, but it does so randomly. Card state here is *derived* by
        # replaying the log, so fuzzing would give the same student different
        # due dates on different rack PCs, and a different answer every time
        # the same log was folded. Our review load is already spread by the
        # school calendar, so there is nothing to fuzz away.
        self._fsrs = scheduler or Scheduler(
            desired_retention=policy.target_retention, enable_fuzzing=False
        )
        self._never_retaught = set(calendar.never_taught(c.id for c in catalog))

    # -- per-task ---------------------------------------------------------

    def assess(
        self,
        task_id: str,
        observations: Iterable[Observation],
        today: date,
    ) -> ReviewItem:
        obs = [o for o in observations if o.task_id == task_id]
        card, n_reviews = replay(obs, self._fsrs)
        never_retaught = task_id in self._never_retaught

        now = datetime(today.year, today.month, today.day, 12, tzinfo=timezone.utc)

        if n_reviews == 0:
            # Nothing to estimate from. Hold it if the class has not reached it
            # yet; otherwise it is the most valuable thing we could ask about.
            taught = self.calendar.taught_through(today)
            held = not never_retaught and task_id not in taught
            return ReviewItem(
                task_id=task_id,
                section=self.catalog[task_id].section,
                urgency=Urgency.HELD if held else Urgency.NEVER_SEEN,
                retrievability=0.0,
                due=None,
                days_overdue=0,
                reviews=0,
                never_retaught=never_retaught,
                priority=0.0 if held else self._priority(0.0, 0, never_retaught, today),
            )

        retrievability = float(self._fsrs.get_card_retrievability(card, current_datetime=now))
        due = card.due.astimezone(timezone.utc).date() if card.due else None
        days_overdue = (today - due).days if due else 0

        if days_overdue > 0:
            urgency = Urgency.OVERDUE
        elif days_overdue == 0 and due is not None:
            urgency = Urgency.SCHEDULED
        elif retrievability < self.policy.target_retention:
            urgency = Urgency.DECAYING
        else:
            urgency = Urgency.SCHEDULED

        return ReviewItem(
            task_id=task_id,
            section=self.catalog[task_id].section,
            urgency=urgency,
            retrievability=retrievability,
            due=due,
            days_overdue=max(0, days_overdue),
            reviews=n_reviews,
            never_retaught=never_retaught,
            priority=self._priority(retrievability, days_overdue, never_retaught, today),
        )

    def _priority(
        self, retrievability: float, days_overdue: int, never_retaught: bool, today: date
    ) -> float:
        """Higher means review sooner.

        Weakest recall first, then how overdue it is, then a bias toward tasks
        the year's lessons will never touch again, then a gentle ramp as the
        exam approaches.
        """
        score = (1.0 - retrievability) + min(days_overdue, 60) / 60.0
        if never_retaught:
            score *= self.policy.never_retaught_weight
        days_left = self.calendar.days_until_exam(Exam.WRITTEN, today)
        if days_left is not None and 0 <= days_left <= self.policy.exam_horizon_days:
            score *= 1.0 + (self.policy.exam_horizon_days - days_left) / self.policy.exam_horizon_days
        return round(score, 4)

    # -- queue ------------------------------------------------------------

    def queue(
        self,
        observations: Sequence[Observation],
        today: date | None = None,
        limit: int | None = None,
        student_id: str | None = None,
    ) -> list[ReviewItem]:
        """The tasks worth reviewing today, most urgent first."""
        today = today or datetime.now(timezone.utc).date()
        by_task: dict[str, list[Observation]] = {}
        for o in observations:
            by_task.setdefault(o.task_id, []).append(o)

        items = [
            self.assess(c.id, by_task.get(c.id, ()), today)
            for c in self.catalog.with_modality(Modality.RECALL)
        ]
        actionable = [i for i in items if i.is_actionable and i.priority > 0]
        actionable.sort(key=lambda i: (-i.priority, int(i.task_id)))
        if student_id is None and observations:
            student_id = observations[0].student_id
        actionable = _interleave(actionable, f"{student_id or ''}:{today.isoformat()}")
        return actionable[: limit or self.policy.max_daily_items]

    def at_risk(
        self, observations: Sequence[Observation], today: date | None = None
    ) -> list[ReviewItem]:
        """Every decaying task, unbounded -- the instructor's cohort view."""
        today = today or datetime.now(timezone.utc).date()
        by_task: dict[str, list[Observation]] = {}
        for o in observations:
            by_task.setdefault(o.task_id, []).append(o)
        items = [
            self.assess(c.id, by_task.get(c.id, ()), today)
            for c in self.catalog.with_modality(Modality.RECALL)
        ]
        return sorted(
            (i for i in items if i.is_actionable and i.retrievability < self.policy.target_retention),
            key=lambda i: (-i.priority, int(i.task_id)),
        )
