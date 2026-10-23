"""Load the packaged grid and year plan into domain objects.

The YAML files are generated (see tools/) and treated as read-only data. This
module is the only place that knows their shape, so the domain layer stays
free of file formats.
"""

from __future__ import annotations

import functools
import pathlib
from datetime import date
from typing import Any, Mapping

import yaml

from .domain.calendar import AcademicCalendar, Exam, ExamWindow, Trimester, Week
from .domain.competency import Catalog, Competency, Modality

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
COMPETENCIES = DATA_DIR / "competencies.yaml"
CALENDAR_2026_2027 = DATA_DIR / "calendar_2026_2027.yaml"


def _read(path: pathlib.Path) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_catalog(path: pathlib.Path | None = None) -> Catalog:
    raw = _read(path or COMPETENCIES)
    tasks = [
        Competency(
            id=t["id"],
            section=t["section"],
            title=t.get("title"),
            modality=tuple(Modality(m) for m in t.get("modality") or ()),
            reserved=bool(t.get("reserved", False)),
            not_taught=t.get("not_taught"),
        )
        for t in raw["tasks"]
    ]
    return Catalog.from_mapping(raw["sections"], tasks)


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def load_calendar(path: pathlib.Path | None = None) -> AcademicCalendar:
    raw = _read(path or CALENDAR_2026_2027)

    weeks = tuple(
        Week(
            trimester=str(w["trimester"]),
            week=str(w["week"]),
            dates_label=w.get("dates_label", ""),
            focus=w.get("focus", ""),
            standards=tuple(str(s) for s in w.get("standards") or ()),
            flags=tuple(w.get("flags") or ()),
            ccna_labs=tuple(str(c) for c in w.get("ccna_labs") or ()),
            activity=w.get("activity", ""),
            notes=w.get("notes", ""),
            start=_as_date(w.get("start")),
            end=_as_date(w.get("end")),
            is_break=bool(w.get("is_break", False)),
        )
        for w in raw["weeks"]
    )

    trimesters = {
        num: Trimester(
            number=str(num),
            start=_as_date(t["start"]),
            end=_as_date(t["end"]),
            student_days=int(t["student_days"]),
        )
        for num, t in raw["trimesters"].items()
    }

    exams = {}
    for name, window in (raw.get("nocti") or {}).items():
        exam = Exam(name)
        exams[exam] = ExamWindow(
            exam=exam,
            window_start=_as_date(window["window_start"]),
            window_end=_as_date(window["window_end"]),
            placeholder=bool(window.get("placeholder", False)),
        )

    return AcademicCalendar(
        year=str(raw["year"]),
        track=str(raw["track"]),
        weeks=weeks,
        trimesters=trimesters,
        exams=exams,
    )


@functools.lru_cache(maxsize=1)
def catalog() -> Catalog:
    """The packaged CIP 11.0901 catalogue (cached)."""
    return load_catalog()


@functools.lru_cache(maxsize=1)
def calendar() -> AcademicCalendar:
    """The packaged 2026-2027 networking-track calendar (cached)."""
    return load_calendar()
