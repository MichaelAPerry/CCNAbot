"""Generate src/ccnabot/data/calendar_2026_2027.yaml from tools/_plan_raw.json.

Provenance: PM Class Year Plan, Networking track (Network+), 2026-2027.
Resolves the plan's `Std.` column into concrete CIP 11.0901 task ids so the
agent can answer "what should this student have covered by now?".

Usage:  python3 tools/build_calendar.py
"""

import json
import pathlib
import re
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "tools" / "_plan_raw.json"
COMPETENCIES = ROOT / "src" / "ccnabot" / "data" / "competencies.yaml"
OUT = ROOT / "src" / "ccnabot" / "data" / "calendar_2026_2027.yaml"

DASH = "–"  # en dash, used throughout the source document

# The plan cites 805 twice (T2 W10, W11) but 805 is RESERVED in the task grid.
# Both cells describe remote access / network management, which is task 806.
# Confirmed with the instructor 2026-09-22.
CORRECTIONS = {"805": ("806", "805 is RESERVED; cells describe remote management (806)")}

TRIMESTERS = {
    "1": {"start": date(2026, 8, 20), "end": date(2026, 11, 13), "student_days": 59},
    "2": {"start": date(2026, 11, 16), "end": date(2027, 2, 26), "student_days": 60},
    "3": {"start": date(2027, 3, 1), "end": date(2027, 5, 28), "student_days": 63},
}

MONTHS = {m: i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1)}


def school_year(month: int) -> int:
    """Aug-Dec fall in 2026; Jan-Jul in 2027."""
    return 2026 if month >= 8 else 2027


def parse_dates(cell: str):
    """'Sep 28-Oct 2' / 'Aug 24-28' / 'Dec 23-Jan 1' -> (start_iso, end_iso)."""
    cell = cell.replace(DASH, "-").strip()
    m = re.match(r"^([A-Z][a-z]{2})\s+(\d{1,2})\s*-\s*(?:([A-Z][a-z]{2})\s+)?(\d{1,2})$", cell)
    if not m:
        return None, None
    m1, d1, m2, d2 = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4))
    s = date(school_year(MONTHS[m1]), MONTHS[m1], d1)
    e = date(school_year(MONTHS[m2]), MONTHS[m2], d2)
    return s.isoformat(), e.isoformat()


def expand(token: str, applied: list):
    """'601-603' -> ['601','602','603']; applies RESERVED corrections."""
    token = token.replace(DASH, "-").strip()
    rng = re.match(r"^(\d{3,4})\s*-\s*(\d{3,4})$", token)
    if rng:
        lo, hi = int(rng.group(1)), int(rng.group(2))
        ids = [str(n) for n in range(lo, hi + 1)]
    elif re.match(r"^\d{3,4}$", token):
        ids = [token]
    else:
        return []
    out = []
    for i in ids:
        if i in CORRECTIONS:
            new, why = CORRECTIONS[i]
            applied.append((i, new, why))
            i = new
        out.append(i)
    return out


def parse_standards(cell: str, applied: list):
    """Returns (task_ids, flags). Flags capture 'cumulative' / section refreshers."""
    cell = cell.strip()
    ids, flags = [], []
    if not cell or cell in ("—", "-"):
        return ids, flags
    for part in cell.split(","):
        part = part.strip()
        if not part:
            continue
        if "cumulative" in part.lower():
            flags.append("cumulative")
            continue
        sec = re.match(r"^(\d{3,4})\s*\(refresher\)$", part)
        if sec:
            flags.append(f"section_refresher:{sec.group(1)}")
            continue
        ids.extend(expand(part, applied))
    return ids, flags


def parse_ccna_labs(cell: str):
    """Extract CCNA lab codes, expanding 'A-1-A-4' style ranges."""
    if "CCNA" not in cell:
        return []
    labs, seen = [], set()
    text = cell.replace(DASH, "~")
    for rng in re.finditer(r"\b(\d{1,2})-(\d{1,2})\s*~\s*(\d{1,2})-(\d{1,2})\b", text):
        ch, lo, ch2, hi = (int(g) for g in rng.groups())
        if ch == ch2:
            for n in range(lo, hi + 1):
                code = f"{ch}-{n}"
                if code not in seen:
                    seen.add(code); labs.append(code)
    for m in re.finditer(r"\b(\d{1,2}-\d{1,2})\b", text.replace("~", " ")):
        code = m.group(1)
        if code not in seen:
            seen.add(code); labs.append(code)
    return labs


def q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> None:
    weeks = json.loads(RAW.read_text())
    known = set(re.findall(r"^  - id: '(\d{3,4})'", COMPETENCIES.read_text(), re.M))
    applied, unknown = [], set()

    rows = []
    for w in weeks:
        ids, flags = parse_standards(w["standards_raw"], applied)
        unknown |= {i for i in ids if i not in known}
        start, end = parse_dates(w["dates"])
        rows.append({**w, "ids": ids, "flags": flags, "start": start, "end": end,
                     "labs": parse_ccna_labs(w["lab"])})

    if unknown:
        raise SystemExit(f"plan references unknown task ids: {sorted(unknown)}")

    lines = [
        "# PM Class Year Plan - Networking track (CompTIA Network+), 2026-2027.",
        "#",
        "# GENERATED FILE - do not hand-edit.",
        "# Regenerate with: python3 tools/build_calendar.py",
        "#",
        "# `standards` resolves the plan's Std. column to CIP 11.0901 task ids.",
        "# NOCTI dates are PLACEHOLDERS pending the testing coordinator.",
        "",
        "year: '2026-2027'",
        "track: networking",
        "certification: CompTIA Network+ (N10-009)",
        "",
        "trimesters:",
    ]
    for num, t in TRIMESTERS.items():
        lines += [f"  '{num}':",
                  f"    start: {t['start'].isoformat()}",
                  f"    end: {t['end'].isoformat()}",
                  f"    student_days: {t['student_days']}"]

    lines += [
        "",
        "# Both NOCTI exams assess the FULL CIP 11.0901 grid, not just this track.",
        "# These are the retention targets the review scheduler works backward from.",
        "nocti:",
        "  practical:",
        "    window_start: 2027-03-15",
        "    window_end: 2027-03-19",
        "    placeholder: true",
        "  written:",
        "    window_start: 2027-04-19",
        "    window_end: 2027-04-23",
        "    placeholder: true",
        "",
        "weeks:",
    ]

    for r in rows:
        lines.append(f"  - trimester: '{r['trimester']}'")
        lines.append(f"    week: {q(r['week'])}")
        if r["start"]:
            lines.append(f"    start: {r['start']}")
            lines.append(f"    end: {r['end']}")
        lines.append(f"    dates_label: {q(r['dates'])}")
        lines.append(f"    focus: {q(r['focus'])}")
        lines.append("    standards: [" + ", ".join(f"'{i}'" for i in r["ids"]) + "]")
        if r["flags"]:
            lines.append("    flags: [" + ", ".join(r["flags"]) + "]")
        if r["labs"]:
            lines.append("    ccna_labs: [" + ", ".join(f"'{l}'" for l in r["labs"]) + "]")
        if r["lab"]:
            lines.append(f"    activity: {q(r['lab'])}")
        if r["notes"]:
            lines.append(f"    notes: {q(r['notes'])}")
        if r["week"] == "—":
            lines.append("    is_break: true")

    OUT.write_text("\n".join(lines) + "\n")

    covered = sorted({i for r in rows for i in r["ids"]}, key=int)
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  weeks:            {len(rows)}")
    print(f"  tasks referenced: {len(covered)}")
    for old, new, why in dict.fromkeys(applied):
        n = sum(1 for a in applied if a[0] == old)
        print(f"  CORRECTION x{n}:    {old} -> {new}  ({why})")


if __name__ == "__main__":
    main()
