"""SQLite-backed append-only observation log.

One store per rack PC. Writes only ever append; the schema's triggers make
that a database-level guarantee rather than a convention.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from datetime import date, datetime, timezone
from typing import Iterable, Iterator

from ..domain.events import EventKind, Observation, Outcome

SCHEMA = pathlib.Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = "1"




class EventLog:
    """Append-only store of observations, plus a small student roster."""

    def __init__(self, path: str | pathlib.Path = ":memory:", source: str = "unknown") -> None:
        self.path = str(path)
        self.source = source
        self._conn = sqlite3.connect(self.path, detect_types=0)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._conn.commit()

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- roster ----------------------------------------------------------

    def upsert_student(
        self,
        student_id: str,
        display_name: str = "",
        track: str = "networking",
        cohort: str = "",
        active: bool = True,
    ) -> None:
        self._conn.execute(
            """INSERT INTO students (student_id, display_name, track, cohort, active)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(student_id) DO UPDATE SET
                 display_name = excluded.display_name,
                 track        = excluded.track,
                 cohort       = excluded.cohort,
                 active       = excluded.active""",
            (student_id, display_name, track, cohort, int(active)),
        )
        self._conn.commit()

    def students(self, active_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM students"
        if active_only:
            sql += " WHERE active = 1"
        return list(self._conn.execute(sql + " ORDER BY student_id"))

    # -- writing ---------------------------------------------------------

    def append(self, observation: Observation) -> Observation:
        """Record one observation. Re-appending a known event_id is a no-op."""
        self._conn.execute(
            """INSERT OR IGNORE INTO observations
               (event_id, student_id, task_id, kind, outcome, occurred_at, source, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation.event_id,
                observation.student_id,
                observation.task_id,
                observation.kind.value,
                observation.outcome.value,
                observation.occurred_at.astimezone(timezone.utc).isoformat(),
                observation.source or self.source,
                json.dumps(dict(observation.detail), sort_keys=True),
            ),
        )
        self._conn.commit()
        return observation

    def extend(self, observations: Iterable[Observation]) -> int:
        """Bulk append. Returns the number of genuinely new rows."""
        before = self.count()
        rows = [
            (
                o.event_id,
                o.student_id,
                o.task_id,
                o.kind.value,
                o.outcome.value,
                o.occurred_at.astimezone(timezone.utc).isoformat(),
                o.source or self.source,
                json.dumps(dict(o.detail), sort_keys=True),
            )
            for o in observations
        ]
        self._conn.executemany(
            """INSERT OR IGNORE INTO observations
               (event_id, student_id, task_id, kind, outcome, occurred_at, source, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self._conn.commit()
        return self.count() - before

    def record(
        self,
        student_id: str,
        task_id: str,
        kind: EventKind,
        outcome: Outcome,
        occurred_at: datetime | None = None,
        **detail: object,
    ) -> Observation:
        """Convenience wrapper that stamps `source` from this store."""
        obs = Observation(
            student_id=student_id,
            task_id=task_id,
            kind=kind,
            outcome=outcome,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            source=self.source,
            detail=detail,
        )
        return self.append(obs)

    # -- reading ---------------------------------------------------------

    @staticmethod
    def _to_observation(row: sqlite3.Row) -> Observation:
        return Observation(
            event_id=row["event_id"],
            student_id=row["student_id"],
            task_id=row["task_id"],
            kind=EventKind(row["kind"]),
            outcome=Outcome(row["outcome"]),
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            source=row["source"],
            detail=json.loads(row["detail"]),
        )

    def __iter__(self) -> Iterator[Observation]:
        return iter(self.observations())

    def __len__(self) -> int:
        return self.count()

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    def observations(
        self,
        student_id: str | None = None,
        task_id: str | None = None,
        since: date | datetime | None = None,
    ) -> list[Observation]:
        sql = "SELECT * FROM observations WHERE 1=1"
        args: list[object] = []
        if student_id is not None:
            sql += " AND student_id = ?"
            args.append(student_id)
        if task_id is not None:
            sql += " AND task_id = ?"
            args.append(task_id)
        if since is not None:
            if isinstance(since, date) and not isinstance(since, datetime):
                since = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
            sql += " AND occurred_at >= ?"
            args.append(since.astimezone(timezone.utc).isoformat())
        sql += " ORDER BY occurred_at, event_id"
        return [self._to_observation(r) for r in self._conn.execute(sql, args)]

    def student_ids(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT student_id FROM observations ORDER BY student_id"
        )
        return [r[0] for r in rows]

    # -- sync ------------------------------------------------------------

    def merge_from(self, other: "EventLog") -> int:
        """Pull every observation from another rack's store.

        Idempotent and order-independent: running it twice, or in both
        directions, converges to the same set.
        """
        return self.extend(other.observations())
