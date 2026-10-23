import pathlib
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from ccnabot.domain.events import EventKind, Observation, Outcome  # noqa: E402
from ccnabot.loader import load_calendar, load_catalog  # noqa: E402


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture(scope="session")
def calendar():
    return load_calendar()


@pytest.fixture
def at():
    """Build a UTC datetime concisely: at(2026, 9, 22)."""
    def _at(y, m, d, hh=12, mm=0):
        return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)
    return _at


@pytest.fixture
def obs(at):
    """Build an Observation with sensible defaults."""
    def _obs(student="ana", task="504", kind=EventKind.DRILL,
             outcome=Outcome.CORRECT, when=None, **detail):
        return Observation(
            student_id=student, task_id=task, kind=kind, outcome=outcome,
            occurred_at=when or at(2026, 9, 22), source="rack3", detail=detail,
        )
    return _obs
