"""Observations: the append-only evidence log.

Every claim the agent makes about a student reduces to a list of observations.
Nothing about a learner is stored as mutable state -- mastery is a *fold* over
these events (see mastery.py).

That choice is forced by the deployment: eight rack PCs each record locally and
no student is pinned to one rack for the year. Append-only logs from N sources
merge by union on `event_id`, with no conflict resolution and no lost updates.
It also means every proficiency mark can be traced back to the evidence that
produced it, which matters when the output is a signed state record.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


class EventKind(enum.Enum):
    """Which assessor produced the observation."""

    DRILL = "drill"                  # generated exercise, machine-graded
    RECALL_REVIEW = "recall_review"  # spaced-repetition review
    RACK_CHECK = "rack_check"        # lab device state / console transcript
    HOST_CHECK = "host_check"        # workstation state
    ARTIFACT_REVIEW = "artifact"     # submitted file, graded against a rubric
    ATTESTATION = "attestation"      # instructor judgement


class Outcome(enum.Enum):
    CORRECT = "correct"
    PARTIAL = "partial"
    INCORRECT = "incorrect"
    ATTESTED = "attested"    # instructor asserts proficiency
    REVOKED = "revoked"      # instructor withdraws a previous attestation

    @property
    def is_success(self) -> bool:
        return self in (Outcome.CORRECT, Outcome.ATTESTED)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Observation:
    """A single immutable piece of evidence about one student and one task."""

    student_id: str
    task_id: str
    kind: EventKind
    outcome: Outcome
    occurred_at: datetime = field(default_factory=_utcnow)
    source: str = "unknown"          # which rack PC recorded this
    detail: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware (use UTC)")
        if not self.student_id:
            raise ValueError("student_id is required")
        if not self.task_id:
            raise ValueError("task_id is required")
        if self.kind is EventKind.ATTESTATION and self.outcome not in (
            Outcome.ATTESTED, Outcome.REVOKED
        ):
            raise ValueError("attestation events must be ATTESTED or REVOKED")
        if self.outcome in (Outcome.ATTESTED, Outcome.REVOKED) and (
            self.kind is not EventKind.ATTESTATION
        ):
            raise ValueError("only attestation events may be ATTESTED or REVOKED")

    @property
    def sort_key(self) -> tuple[datetime, str]:
        """Total order that is stable across merges from several rack PCs."""
        return (self.occurred_at, self.event_id)


def merge(*streams: Iterable[Observation]) -> list[Observation]:
    """Union observations from any number of sources, de-duplicated by event_id.

    Safe to call repeatedly with overlapping inputs; the result is the same
    regardless of argument order, which is what lets rack PCs sync pairwise.
    """
    seen: dict[str, Observation] = {}
    for stream in streams:
        for obs in stream:
            seen.setdefault(obs.event_id, obs)
    return sorted(seen.values(), key=lambda o: o.sort_key)
