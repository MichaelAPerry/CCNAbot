"""The CIP 11.0901 competency catalogue.

Task ids are the primary keys for the whole system. They come from the state
task list and must never be renumbered locally: a student's record, the
instructor's signed report and the NOCTI blueprint all key on them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, Sequence


class Modality(enum.Enum):
    """What kind of evidence can demonstrate a competency.

    This determines which assessor may mark a task, and it is the reason the
    agent cannot simply watch the rack: most of the grid is not observable
    from a console at all.
    """

    RECALL = "recall"      # declarative knowledge; quizzed and spaced
    RACK = "rack"          # lab gear, via console transcript or device state
    HOST = "host"          # the Linux workstation the agent runs on
    ARTIFACT = "artifact"  # a file the student submits (diagram, pcap, doc)
    ATTEST = "attest"      # physical or behavioural; only an instructor can mark it

    @property
    def is_machine_observable(self) -> bool:
        return self is not Modality.ATTEST


@dataclass(frozen=True, slots=True)
class Competency:
    """One numbered task from the state grid."""

    id: str
    section: str
    title: str | None
    modality: tuple[Modality, ...] = ()
    reserved: bool = False
    not_taught: str | None = None

    @property
    def primary_modality(self) -> Modality | None:
        return self.modality[0] if self.modality else None

    @property
    def needs_instructor(self) -> bool:
        """True when no automated assessor can ever produce evidence for this."""
        return bool(self.modality) and all(m is Modality.ATTEST for m in self.modality)

    def supports(self, modality: Modality) -> bool:
        return modality in self.modality

    def __str__(self) -> str:
        return f"{self.id} {self.title or 'RESERVED'}"


@dataclass(frozen=True, slots=True)
class Catalog:
    """An immutable, indexed view over the competency grid."""

    sections: Mapping[str, str]
    _by_id: Mapping[str, Competency] = field(repr=False)

    @classmethod
    def from_mapping(cls, sections: Mapping[str, str], tasks: Iterable[Competency]) -> "Catalog":
        by_id: dict[str, Competency] = {}
        for t in tasks:
            if t.id in by_id:
                raise ValueError(f"duplicate task id {t.id!r}")
            by_id[t.id] = t
        return cls(sections=dict(sections), _by_id=by_id)

    def __iter__(self) -> Iterator[Competency]:
        """Iterate live tasks in grid order. RESERVED entries are skipped."""
        return iter(sorted(
            (c for c in self._by_id.values() if not c.reserved),
            key=lambda c: int(c.id),
        ))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, task_id: object) -> bool:
        """Membership means *addressable*, matching __getitem__ and get().

        RESERVED slots deliberately fail this test. They exist in the state
        grid but nothing can be assessed against them, and letting `in`
        disagree with `[]` would allow evidence to be accepted against a
        reserved id and then silently dropped. Use knows() to ask whether an
        id appears in the grid at all.
        """
        return isinstance(task_id, str) and self.get(task_id) is not None

    def knows(self, task_id: str) -> bool:
        """True if the id appears in the grid, RESERVED slots included."""
        return task_id in self._by_id

    def is_reserved(self, task_id: str) -> bool:
        entry = self._by_id.get(task_id)
        return entry is not None and entry.reserved

    def __getitem__(self, task_id: str) -> Competency:
        try:
            c = self._by_id[task_id]
        except KeyError:
            raise KeyError(f"no such task {task_id!r} in CIP 11.0901") from None
        if c.reserved:
            raise KeyError(f"task {task_id!r} is RESERVED in the state grid")
        return c

    def get(self, task_id: str) -> Competency | None:
        c = self._by_id.get(task_id)
        return None if c is None or c.reserved else c

    @property
    def all_including_reserved(self) -> Sequence[Competency]:
        return sorted(self._by_id.values(), key=lambda c: int(c.id))

    def section_of(self, task_id: str) -> str:
        return self.sections[self[task_id].section]

    def in_section(self, section: str) -> list[Competency]:
        return [c for c in self if c.section == section]

    def with_modality(self, modality: Modality) -> list[Competency]:
        return [c for c in self if c.supports(modality)]

    def requiring_instructor(self) -> list[Competency]:
        return [c for c in self if c.needs_instructor]
