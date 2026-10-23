"""Generate src/ccnabot/data/competencies.yaml from the extracted CIP 11.0901 grid.

Provenance: the task numbers and titles come verbatim from the PDE Competency
Task List for CIP 11.0901 (Computer Systems Networking and Telecommunications),
High School Graduation Years 2022/2023/2024. `tools/_grid_raw.json` is a
machine extraction of that document, kept so this file can be regenerated and
diffed rather than hand-edited.

The MODALITY map below is *our* editorial layer, not the state's. It records
what could possibly produce evidence for each task, which determines which
assessor can mark it. See docs/modality.md.

Usage:  python3 tools/build_competencies.py
"""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "tools" / "_grid_raw.json"
OUT = ROOT / "src" / "ccnabot" / "data" / "competencies.yaml"

# Assessment modality per task. First entry is primary.
#   recall   - declarative knowledge; quizzable, scheduled by FSRS
#   rack     - observable on lab gear via console transcript / device state
#   host     - observable on the Linux workstation the agent runs on
#   artifact - student submits a file (diagram, .pcap, written doc)
#   attest   - physical or behavioural; only an instructor can mark it
MODALITY = {
    # 100 Personal and Environmental Safety
    "101": ["recall"], "102": ["attest"], "103": ["recall"], "104": ["recall"],
    "105": ["recall"], "106": ["attest"], "107": ["recall"], "108": ["recall"],
    "109": ["recall"], "110": ["recall"], "111": ["attest"], "113": ["host"],
    "114": ["attest"],
    # 200 Computer Hardware
    "201": ["recall"], "202": ["recall"], "203": ["recall"], "204": ["recall"],
    "205": ["recall"], "206": ["recall"], "207": ["recall"], "208": ["recall"],
    "209": ["recall"], "210": ["attest"], "211": ["attest"], "212": ["attest"],
    "213": ["attest"], "214": ["attest"], "216": ["attest"],
    # 300 Troubleshooting, Repair and Maintenance
    "301": ["recall", "artifact"], "302": ["attest"], "304": ["attest"],
    "305": ["attest"], "307": ["attest"],
    # 400 Operating Systems and Software
    "401": ["recall"], "402": ["host"], "403": ["attest", "host"],
    "404": ["recall"], "405": ["host"], "406": ["host", "recall"],
    "407": ["host"], "408": ["host"], "409": ["host"], "410": ["attest", "host"],
    # 500 Network Technologies
    "501": ["recall"], "502": ["recall"], "503": ["recall"], "504": ["recall"],
    "505": ["recall"], "506": ["rack", "recall"], "507": ["recall"],
    "508": ["recall"], "509": ["recall"], "510": ["host", "recall"],
    # 600 Network Media and Topologies
    "601": ["recall"], "602": ["recall"], "603": ["recall"], "604": ["attest"],
    "605": ["recall"], "606": ["recall"], "607": ["recall"], "608": ["attest"],
    # 700 Network Devices
    "701": ["rack"], "702": ["recall"], "703": ["rack"],
    "704": ["attest"], "705": ["attest"],
    # 800 Network Management
    "801": ["recall"], "802": ["artifact"], "803": ["artifact"],
    "804": ["artifact", "rack"], "806": ["rack"],
    # 900 Network Tools and Troubleshooting
    "901": ["rack", "host"], "902": ["artifact", "host"], "903": ["attest"],
    "904": ["rack", "artifact"], "905": ["artifact"], "906": ["rack"],
    "907": ["rack"],
    # 1000 Security Fundamentals
    "1001": ["rack"], "1002": ["rack", "recall"], "1003": ["rack"],
    "1004": ["recall", "rack"], "1005": ["recall"], "1006": ["recall"],
    "1007": ["host", "attest"], "1008": ["artifact", "recall"],
    # 1100 Communication and Professionalism
    "1101": ["attest"], "1102": ["attest"], "1103": ["recall"],
    "1104": ["recall"], "1105": ["artifact"],
}

# Tasks the 2026-27 plan deliberately drops (no wireless access point in the rack).
NOT_TAUGHT = {"704": "no WAP in lab inventory", "705": "no WAP in lab inventory"}


def yaml_escape(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> None:
    grid = json.loads(RAW.read_text())
    sections, tasks = grid["sections"], grid["tasks"]

    known = set(MODALITY)
    live = {t["id"] for t in tasks if t["title"].upper() != "RESERVED"}
    if known != live:
        raise SystemExit(
            f"modality map out of sync with grid:\n"
            f"  missing from map: {sorted(live - known)}\n"
            f"  unknown in map:   {sorted(known - live)}"
        )

    lines = [
        "# CIP 11.0901 Competency Task List - Computer Systems Networking",
        "# and Telecommunications. Secondary Component.",
        "#",
        "# GENERATED FILE - do not hand-edit.",
        "# Regenerate with: python3 tools/build_competencies.py",
        "#",
        "# Task ids and titles are verbatim from the PDE task list and are the",
        "# primary keys for the whole system. `modality` is our editorial layer:",
        "# what can produce evidence for the task. See docs/modality.md.",
        "",
        "sections:",
    ]
    for num in sorted(sections, key=int):
        lines.append(f"  '{num}': {yaml_escape(sections[num])}")

    lines += ["", "tasks:"]
    for t in sorted(tasks, key=lambda x: int(x["id"])):
        tid, title = t["id"], t["title"]
        reserved = title.upper() == "RESERVED"
        lines.append(f"  - id: '{tid}'")
        lines.append(f"    section: '{t['section']}'")
        if reserved:
            lines.append("    reserved: true")
            lines.append("    title: null")
            lines.append("    modality: []")
        else:
            lines.append(f"    title: {yaml_escape(title)}")
            mods = MODALITY[tid]
            lines.append("    modality: [" + ", ".join(mods) + "]")
            if tid in NOT_TAUGHT:
                lines.append(f"    not_taught: {yaml_escape(NOT_TAUGHT[tid])}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")

    n_reserved = sum(1 for t in tasks if t["title"].upper() == "RESERVED")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  sections: {len(sections)}")
    print(f"  tasks:    {len(tasks)} ({n_reserved} reserved, {len(tasks) - n_reserved} live)")


if __name__ == "__main__":
    main()
