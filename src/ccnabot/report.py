"""The signed proficiency report.

This is what the system is ultimately *for*. Everything else -- drills, the
review queue, the console tap -- exists to put defensible marks in this
document. It mirrors the state form: a binary (X) per task, then an instructor
signature and a student signature.

The `evidence` view is the payoff of keeping an append-only log. Before an
instructor signs, they can see exactly what each mark rests on: how many
attempts, over how many days, most recent when. A proficiency claim nobody can
audit is not worth signing.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

from .domain.competency import Catalog, Modality
from .domain.mastery import LearnerRecord

PROGRAM = "Computer Systems Networking and Telecommunications"
CIP = "11.0901"
FOOTNOTE = "Student Demonstrated Entry-Level Industry Proficiency as Indicated by (X)"
WIDTH = 96


@dataclass(frozen=True, slots=True)
class ReportOptions:
    show_evidence: bool = False
    show_untouched: bool = True
    today: date | None = None


def _wrap(text: str, width: int, indent: str) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return [lines[0]] + [indent + l for l in lines[1:]] if lines else [""]


def render(
    record: LearnerRecord,
    catalog: Catalog,
    display_name: str = "",
    options: ReportOptions | None = None,
) -> str:
    """Render the competency task list for one student, as a printable form."""
    opts = options or ReportOptions()
    out: list[str] = []
    name = display_name or record.student_id

    out.append("=" * WIDTH)
    out.append(f"COMPETENCY TASK LIST - Secondary Component".center(WIDTH))
    out.append(f"{PROGRAM}  CIP {CIP}".center(WIDTH))
    out.append("=" * WIDTH)
    out.append(f"Student: {name}"
               f"{'':>8}Generated: {(opts.today or date.today()).isoformat()}")
    marked = len(record.proficient_ids)
    out.append(f"Proficient: {marked} of {len(record.tasks)} tasks "
               f"({record.coverage() * 100:.0f}%)")
    out.append("")

    for section_id, section_name in sorted(catalog.sections.items(), key=lambda kv: int(kv[0])):
        tasks = catalog.in_section(section_id)
        if not tasks:
            continue
        done = sum(1 for t in tasks if record.tasks[t.id].proficient)
        out.append("-" * WIDTH)
        out.append(f"{section_id} {section_name}  [{done}/{len(tasks)}]")
        out.append("-" * WIDTH)

        for task in tasks:
            m = record.tasks[task.id]
            if not opts.show_untouched and not m.has_evidence:
                continue
            mark = "X" if m.proficient else " "
            head = f" ({mark})  {task.id}  "
            body = _wrap(task.title or "", WIDTH - len(head), " " * len(head))
            out.append(head + body[0])
            out.extend(body[1:])

            if opts.show_evidence and m.has_evidence:
                if m.attested:
                    out.append(f"{'':>13}evidence: attested by {m.attested_by} "
                               f"on {m.attested_at.date().isoformat()}")
                else:
                    last = m.last_success.date().isoformat() if m.last_success else "never"
                    out.append(f"{'':>13}evidence: {m.successes}/{m.attempts} correct "
                               f"across {m.distinct_days} day(s), last success {last}")
        out.append("")

    out.append("=" * WIDTH)
    out.append(f"1 {FOOTNOTE}")
    out.append("")
    out.append("Secondary CTE Instructor Signature " + "_" * 40 + "  Date " + "_" * 12)
    out.append("")
    out.append("Student Signature " + "_" * 56 + "  Date " + "_" * 12)
    out.append("=" * WIDTH)
    return "\n".join(out)


def to_csv(record: LearnerRecord, catalog: Catalog) -> str:
    """Machine-readable export, for handoff to a gradebook or LMS."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([
        "task_id", "section", "section_name", "title", "modality",
        "proficient", "attested_by", "attempts", "successes",
        "distinct_days", "last_success",
    ])
    for task in catalog:
        m = record.tasks[task.id]
        w.writerow([
            task.id, task.section, catalog.sections[task.section], task.title,
            "|".join(x.value for x in task.modality),
            "X" if m.proficient else "",
            m.attested_by or "",
            m.attempts, m.successes, m.distinct_days,
            m.last_success.date().isoformat() if m.last_success else "",
        ])
    return buf.getvalue()


def outstanding_by_modality(
    record: LearnerRecord, catalog: Catalog
) -> dict[Modality, list[str]]:
    """Not-yet-proficient tasks grouped by who can actually mark them.

    The instructor's practical question at report time is "what is left, and
    is any of it mine to sign off?" -- the ~23 physical tasks cannot be
    cleared by any amount of student work at a terminal.
    """
    out: dict[Modality, list[str]] = {m: [] for m in Modality}
    for task in catalog:
        if record.tasks[task.id].proficient:
            continue
        primary = task.primary_modality
        if primary is not None:
            out[primary].append(task.id)
    return {k: v for k, v in out.items() if v}
