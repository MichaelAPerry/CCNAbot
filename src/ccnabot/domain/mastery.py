"""Mastery: folding observations into a proficiency judgement.

Two different questions get confused constantly, so this module keeps them
apart:

  proficient  Did the student ever clear the evidence bar for this task?
              This is the (X) on the signed state form. It is monotonic --
              once earned it is not lost to forgetting, only to an instructor
              revoking it. A skill demonstrated in October was still
              demonstrated in April.

  decayed     Is the student likely to still recall it *today*? This is what
              the tutor schedules against, and it has nothing to do with the
              state record. See scheduling/retention.py.

Conflating them produces either a report that silently un-marks earned
competencies, or a tutor that never reviews anything a student once passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

from .competency import Catalog, Competency
from .events import EventKind, Observation, Outcome


@dataclass(frozen=True, slots=True)
class MasteryPolicy:
    """When does accumulated evidence amount to proficiency?

    The defaults encode ordinary mastery-learning practice:

    * `min_successes` > 1 so a single lucky attempt cannot mark a task.
    * `min_distinct_days` > 1 so massed practice in one sitting cannot either.
      Three correct answers in four minutes is recognition, not retention.
    * `min_success_rate` so that three successes buried in twenty failures
      does not clear the bar.

    Instructor attestation bypasses all of it -- their signature is the
    authority on the form, and for ~23 physical tasks it is the only possible
    evidence.
    """

    min_successes: int = 3
    min_distinct_days: int = 2
    min_success_rate: float = 0.75

    def __post_init__(self) -> None:
        if self.min_successes < 1:
            raise ValueError("min_successes must be >= 1")
        if self.min_distinct_days < 1:
            raise ValueError("min_distinct_days must be >= 1")
        if not 0.0 <= self.min_success_rate <= 1.0:
            raise ValueError("min_success_rate must be in [0, 1]")


DEFAULT_POLICY = MasteryPolicy()


@dataclass(frozen=True, slots=True)
class TaskMastery:
    """What the evidence says about one student and one task."""

    student_id: str
    task_id: str
    attempts: int = 0
    successes: int = 0
    distinct_days: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    last_success: datetime | None = None
    attested_by: str | None = None
    attested_at: datetime | None = None
    proficient: bool = False
    evidence: tuple[str, ...] = ()

    @property
    def attested(self) -> bool:
        return self.attested_by is not None

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def has_evidence(self) -> bool:
        return self.attempts > 0 or self.attested

    def days_since_success(self, today: date | None = None) -> int | None:
        if self.last_success is None:
            return None
        today = today or datetime.now(self.last_success.tzinfo).date()
        return (today - self.last_success.date()).days


def fold_task(
    student_id: str,
    task_id: str,
    observations: Iterable[Observation],
    policy: MasteryPolicy = DEFAULT_POLICY,
) -> TaskMastery:
    """Reduce every observation for one (student, task) to a mastery judgement."""
    obs = sorted(observations, key=lambda o: o.sort_key)

    attempts = successes = 0
    days: set[date] = set()
    first = last = last_success = None
    attested_by = attested_at = None
    evidence: list[str] = []

    for o in obs:
        if o.student_id != student_id or o.task_id != task_id:
            raise ValueError("observation does not belong to this student/task")

        first = first or o.occurred_at
        last = o.occurred_at

        if o.kind is EventKind.ATTESTATION:
            # Latest attestation wins; a revocation clears an earlier mark.
            if o.outcome is Outcome.ATTESTED:
                attested_by = str(o.detail.get("instructor", o.source))
                attested_at = o.occurred_at
                evidence.append(o.event_id)
            else:
                attested_by = attested_at = None
            continue

        attempts += 1
        if o.outcome.is_success:
            successes += 1
            last_success = o.occurred_at
            days.add(o.occurred_at.date())
            evidence.append(o.event_id)

    earned = (
        successes >= policy.min_successes
        and len(days) >= policy.min_distinct_days
        and (successes / attempts if attempts else 0.0) >= policy.min_success_rate
    )

    return TaskMastery(
        student_id=student_id,
        task_id=task_id,
        attempts=attempts,
        successes=successes,
        distinct_days=len(days),
        first_seen=first,
        last_seen=last,
        last_success=last_success,
        attested_by=attested_by,
        attested_at=attested_at,
        proficient=bool(attested_by) or earned,
        evidence=tuple(evidence),
    )


@dataclass(frozen=True, slots=True)
class LearnerRecord:
    """Every task's mastery for one student, derived purely from observations."""

    student_id: str
    tasks: Mapping[str, TaskMastery] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        student_id: str,
        observations: Iterable[Observation],
        catalog: Catalog,
        policy: MasteryPolicy = DEFAULT_POLICY,
    ) -> "LearnerRecord":
        grouped: dict[str, list[Observation]] = {}
        for o in observations:
            if o.student_id != student_id:
                continue
            if o.task_id not in catalog:
                raise ValueError(f"observation references unknown task {o.task_id!r}")
            grouped.setdefault(o.task_id, []).append(o)

        tasks = {
            c.id: fold_task(student_id, c.id, grouped.get(c.id, ()), policy)
            for c in catalog
        }
        return cls(student_id=student_id, tasks=tasks)

    def __getitem__(self, task_id: str) -> TaskMastery:
        return self.tasks[task_id]

    @property
    def proficient_ids(self) -> list[str]:
        return sorted((t for t, m in self.tasks.items() if m.proficient), key=int)

    @property
    def untouched_ids(self) -> list[str]:
        """Tasks with no evidence at all -- the honest 'not started' set."""
        return sorted((t for t, m in self.tasks.items() if not m.has_evidence), key=int)

    def outstanding(self, catalog: Catalog) -> list[Competency]:
        """Live tasks not yet proficient, in grid order."""
        return [c for c in catalog if not self.tasks[c.id].proficient]

    def coverage(self) -> float:
        return len(self.proficient_ids) / len(self.tasks) if self.tasks else 0.0


def cohort_coverage(
    records: Sequence[LearnerRecord], task_id: str
) -> float:
    """Fraction of a cohort proficient in one task -- the instructor's view of
    which competencies the class as a whole has not got yet."""
    if not records:
        return 0.0
    hit = sum(1 for r in records if r.tasks.get(task_id, TaskMastery("", task_id)).proficient)
    return hit / len(records)
