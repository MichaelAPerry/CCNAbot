# CCNAbot

A rack assistant for a CCNA / CompTIA Network+ lab.

One instance runs on the Linux PC in each of eight racks. Students SSH in and
work in two sessions side by side: a console into the lab gear, and this.

**The instructor keeps the judgement.** The bot observes, scaffolds and
reports what it saw. It does not grade, does not decide proficiency, and does
not sign anything. Where the state competency form needs an (X), that mark
comes from an instructor attestation — evidence the bot gathered can *suggest*
a task is ready for sign-off, but never applies one itself.

## Status

Foundations only. **The rack integration — the actual point — is not built
yet**, pending a decision on how the device consoles are wired (access server
with reverse-telnet, versus USB-serial straight into the rack PC).

What exists today:

| Piece | What it does |
|---|---|
| `domain/competency.py` | The CIP 11.0901 task grid, 91 live tasks, as typed objects |
| `domain/calendar.py` | The 2026-27 year plan: which tasks are taught when |
| `domain/events.py` | Append-only observations — the evidence log |
| `domain/mastery.py` | Folds evidence into a recommendation for the instructor |
| `store/` | SQLite event log, append-only at the database level |
| `drills/subnetting.py` | Generated subnetting practice for task 504 |
| `scheduling/retention.py` | Optional spaced review of prior-year content |
| `report.py` | The competency task list, for an instructor to review and sign |

## Design notes

**Evidence is append-only.** Nothing about a student is stored as mutable
state; everything is a fold over timestamped observations. Two reasons: eight
racks each record locally and no student is pinned to one bench, so logs must
merge by union without conflict resolution; and every mark on a signed form
should be traceable to the evidence behind it.

**Derived state must be reproducible.** FSRS interval fuzzing is disabled for
this reason — the same log has to rebuild the same schedule on any rack.

**Facts come from the rule engine, never from a language model.** Subnetting
answers come from `ipaddress`; device facts will come from parsed device
state. A tutor that invents plausible-but-wrong IOS syntax is worse than no
tutor, because students trust it and then debug a phantom.

**Task numbers are primary keys.** They come from the state grid and are never
renumbered locally. The `805` cited twice in the year plan is RESERVED in the
grid; it means `806`, and the correction is applied explicitly in
`tools/build_calendar.py` rather than silently.

## Use

```bash
pip install -e ".[dev]"

ccnabot status                      # where the class is in the year
ccnabot drill -n 8                  # subnetting practice (task 504)
ccnabot review                      # optional spaced review queue
ccnabot report --evidence           # what the bot saw, for instructor review
ccnabot attest --student ana --task 604   # instructor marks proficiency
```

Data lives in `$CCNABOT_DB` (default: XDG data dir). It contains student
records — keep it off shared storage and out of version control.

## Regenerating the data files

`src/ccnabot/data/*.yaml` are generated, not hand-edited:

```bash
python3 tools/build_competencies.py   # task grid + assessment modality
python3 tools/build_calendar.py       # year plan -> task crosswalk
```
