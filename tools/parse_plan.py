"""Parse the Networking-track tables out of the PM Class Year Plan docx text.

Emits tools/_plan_raw.json for build_calendar.py. Kept separate from the
generator so the extraction can be re-run if the plan document changes.
"""
import json, re, pathlib, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "plan.txt"
RANGES = {"1": (233, 346), "2": (464, 592), "3": (727, 840)}

def cells(block):
    """Split a ---ROW--- delimited block into rows of cell strings."""
    rows = []
    for chunk in block.split("---ROW---"):
        parts = [p.strip() for p in chunk.split("|")]
        parts = [re.sub(r"\s+", " ", p) for p in parts if p.strip()]
        if parts:
            rows.append(parts)
    return rows

def main():
    lines = pathlib.Path(SRC).read_text(encoding="utf-8").split("\n")
    weeks = []
    for tri, (a, b) in RANGES.items():
        for row in cells("\n".join(lines[a - 1 : b])):
            if len(row) < 4 or row[0] in ("Week", "Networking Track", "Security Track"):
                continue
            if not re.match(r"^(\d{1,2}|—)$", row[0]):
                continue
            weeks.append({
                "trimester": tri,
                "week": row[0],
                "dates": row[1] if len(row) > 1 else "",
                "focus": row[2] if len(row) > 2 else "",
                "standards_raw": row[3] if len(row) > 3 else "",
                "lab": row[4] if len(row) > 4 else "",
                "notes": row[5] if len(row) > 5 else "",
            })
    pathlib.Path("tools/_plan_raw.json").write_text(json.dumps(weeks, indent=1, ensure_ascii=False))
    print(f"parsed {len(weeks)} networking-track weeks")
    for w in weeks:
        print(f"  T{w['trimester']} W{w['week']:>2}  {w['dates']:<14} std={w['standards_raw']!r}")

if __name__ == "__main__":
    main()
