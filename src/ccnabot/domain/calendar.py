"""The academic calendar: what has been taught, and how long until it matters.

The year plan's `Std.` column is the crosswalk between wall-clock time and the
competency grid. It answers the question the tutor needs before it can say
anything useful: *should this student have seen this yet?* Reviewing material
a class has not reached is not scaffolding, it is noise.

It also carries the NOCTI windows, which are the deadlines the retention
scheduler works backward from.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Mapping, Sequence


class Exam(enum.Enum):
    PRACTICAL = "practical"
    WRITTEN = "written"


@dataclass(frozen=True, slots=True)
class ExamWindow:
    exam: Exam
    window_start: date
    window_end: date
    placeholder: bool = True

    def days_until(self, today: date) -> int:
        """Days until the window opens; negative once it has passed."""
        return (self.window_start - today).days


@dataclass(frozen=True, slots=True)
class Week:
    """One instructional week of the plan."""

    trimester: str
    week: str
    dates_label: str
    focus: str
    standards: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    ccna_labs: tuple[str, ...] = ()
    activity: str = ""
    notes: str = ""
    start: date | None = None
    end: date | None = None
    is_break: bool = False

    @property
    def is_cumulative(self) -> bool:
        return "cumulative" in self.flags

    @property
    def label(self) -> str:
        return f"T{self.trimester} W{self.week}"

    def contains(self, day: date) -> bool:
        return bool(self.start and self.end and self.start <= day <= self.end)


@dataclass(frozen=True, slots=True)
class Trimester:
    number: str
    start: date
    end: date
    student_days: int


@dataclass(frozen=True, slots=True)
class AcademicCalendar:
    year: str
    track: str
    weeks: tuple[Week, ...]
    trimesters: Mapping[str, Trimester]
    exams: Mapping[Exam, ExamWindow] = field(default_factory=dict)

    def __iter__(self):
        return iter(self.weeks)

    @property
    def instructional_weeks(self) -> list[Week]:
        return [w for w in self.weeks if not w.is_break]

    @property
    def start(self) -> date:
        return min(t.start for t in self.trimesters.values())

    @property
    def end(self) -> date:
        return max(t.end for t in self.trimesters.values())

    def week_of(self, day: date) -> Week | None:
        for w in self.weeks:
            if w.contains(day):
                return w
        return None

    def weeks_through(self, day: date) -> list[Week]:
        """Every week that has started on or before `day`."""
        return [w for w in self.weeks if w.start and w.start <= day]

    def taught_through(self, day: date) -> set[str]:
        """Task ids the plan has covered by `day`.

        The tutor uses this to avoid drilling students on content the class
        has not reached, and to notice content it has passed.
        """
        return {t for w in self.weeks_through(day) for t in w.standards}

    def scheduled_for(self, task_id: str) -> list[Week]:
        return [w for w in self.weeks if task_id in w.standards]

    def first_taught(self, task_id: str) -> date | None:
        weeks = [w for w in self.scheduled_for(task_id) if w.start]
        return min(w.start for w in weeks) if weeks else None

    def never_taught(self, live_task_ids: Iterable[str]) -> list[str]:
        """Live tasks the plan never schedules.

        These are the ones a student met in a previous year and will meet
        again only on the NOCTI. They are the retention layer's whole reason
        for existing.
        """
        covered = {t for w in self.weeks for t in w.standards}
        return sorted((t for t in live_task_ids if t not in covered), key=int)

    def exam(self, which: Exam) -> ExamWindow | None:
        return self.exams.get(which)

    def days_until_exam(self, which: Exam, today: date) -> int | None:
        window = self.exams.get(which)
        return window.days_until(today) if window else None

    def next_exam(self, today: date) -> ExamWindow | None:
        upcoming = sorted(
            (e for e in self.exams.values() if e.days_until(today) >= 0),
            key=lambda e: e.window_start,
        )
        return upcoming[0] if upcoming else None
