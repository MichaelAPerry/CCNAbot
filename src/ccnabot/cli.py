"""Command-line interface.

Students reach this over SSH on the rack PC, in a second session alongside
their console window. Identity is the POSIX username -- there is no separate
login to build or forget.
"""

from __future__ import annotations

import argparse
import getpass
import os
import pathlib
import socket
import sys
from datetime import date, datetime, timezone

from . import report as report_mod
from .domain.calendar import Exam
from .domain.competency import Modality
from .domain.events import EventKind, Outcome
from .domain.mastery import LearnerRecord
from .drills import subnetting
from .rack import console as console_mod
from .rack import udev as udev_mod
from .rack.inventory import build_rack, load_rack
from .loader import calendar, catalog
from .scheduling.retention import RetentionScheduler, Urgency
from .store.eventlog import EventLog


def default_db() -> pathlib.Path:
    if env := os.environ.get("CCNABOT_DB"):
        return pathlib.Path(env)
    root = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local/share"))
    return root / "ccnabot" / "observations.db"


def rack_id() -> str:
    return os.environ.get("CCNABOT_RACK") or socket.gethostname()


def open_log(args) -> EventLog:
    path = pathlib.Path(args.db) if args.db else default_db()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    return EventLog(path, source=rack_id())


RACK_CONFIG = pathlib.Path("/etc/ccnabot/rack.yaml")


def load_local_rack():
    """The rack this PC is wired to.

    Falls back to an unwired rack so `rack discover` and `rack udev` work on
    a machine that has not been configured yet -- which is exactly when they
    are needed.
    """
    path = pathlib.Path(os.environ.get("CCNABOT_RACK_CONFIG", RACK_CONFIG))
    if path.exists():
        return load_rack(path)
    return build_rack(rack_id())


def whoami(args) -> str:
    return args.student or os.environ.get("CCNABOT_STUDENT") or getpass.getuser()


# -- status --------------------------------------------------------------

def cmd_status(args) -> int:
    cal, today = calendar(), args.today or date.today()
    week = cal.week_of(today)
    print(f"CCNAbot - {cal.year} {cal.track} track ({rack_id()})")
    print(f"Today: {today.isoformat()}")
    if week:
        print(f"Week:  {week.label}  {week.dates_label} - {week.focus}")
        if week.standards:
            print(f"       standards: {', '.join(week.standards)}")
        if week.ccna_labs:
            print(f"       CCNA labs: {', '.join(week.ccna_labs)}")
    else:
        print("Week:  outside the instructional calendar")

    for exam in (Exam.PRACTICAL, Exam.WRITTEN):
        days = cal.days_until_exam(exam, today)
        window = cal.exam(exam)
        if days is None or window is None:
            continue
        note = " (placeholder date)" if window.placeholder else ""
        when = f"in {days} days" if days >= 0 else f"{-days} days ago"
        print(f"NOCTI {exam.value:<10} {window.window_start.isoformat()}  {when}{note}")

    cat = catalog()
    never = cal.never_taught(c.id for c in cat)
    print(f"\nGrid: {len(cat)} live tasks, {len(never)} never retaught this year "
          f"(prior-year content, still on NOCTI)")
    return 0


# -- console -------------------------------------------------------------

def cmd_console(args) -> int:
    student, rack = whoami(args), load_local_rack()
    with open_log(args) as log:
        log.reap_stale_sessions(rack.id)
        try:
            return console_mod.attach(
                rack, args.device, student, log,
                record_transcript=not args.no_record,
                terminal=args.terminal,
            )
        except console_mod.ConsoleUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1


# -- rack ----------------------------------------------------------------

def cmd_rack_status(args) -> int:
    rack = load_local_rack()
    with open_log(args) as log:
        log.reap_stale_sessions(rack.id)
        busy = {r["device"]: r["student_id"] for r in log.open_sessions(rack.id)}

    print(f"{rack.id} - {len(rack)} devices, {len(rack.cabled)} cabled\n")
    print(f"{'device':<7}{'model':<22}{'console':<28}{'state':<12}who")
    print("-" * 82)
    for device in rack:
        if not device.serial:
            print(f"{device.name:<7}{device.model:<22}{'(no cable registered)':<28}"
                  f"{'-':<12}")
            continue
        node = rack.console(device.name)
        state = console_mod.probe(node)
        who = busy.get(device.name.upper(), "")
        label = "in use" if who else state.state.value
        shown = str(node)
        if len(shown) > 27:
            shown = "..." + shown[-24:]
        print(f"{device.name:<7}{device.model:<22}{shown:<28}{label:<12}{who}")

    if rack.uncabled:
        print(f"\n{len(rack.uncabled)} device(s) not yet cabled: "
              f"{', '.join(d.name for d in rack.uncabled)}")
    return 0


def cmd_rack_discover(args) -> int:
    adapters = udev_mod.discover()
    if not adapters:
        print("No USB-serial adapters found.")
        print("Check the hub is powered and the cables are seated.")
        return 1

    print(f"{len(adapters)} adapter(s) attached:\n")
    print(f"{'tty':<10}{'serial':<20}{'vendor':<9}description")
    print("-" * 70)
    for a in adapters:
        vendor = "FTDI" if a.is_ftdi else (a.vendor or "?")
        print(f"{a.tty:<10}{(a.serial or '(none)'):<20}{vendor:<9}{a.description}")

    dupes = udev_mod.duplicate_serials(adapters)
    if dupes:
        print("\nWARNING: duplicate serial numbers found:")
        for serial, ttys in dupes.items():
            print(f"  {serial}: {', '.join(ttys)}")
        print("These adapters cannot be pinned to stable names, and the")
        print("assistant would confuse the devices behind them. Counterfeit")
        print("FTDI chips commonly share one serial -- replace them.")
        return 1

    unpinnable = [a for a in adapters if not a.pinnable]
    if unpinnable:
        print(f"\nWARNING: {len(unpinnable)} adapter(s) report no serial number "
              "and cannot be pinned.")
        return 1

    print("\nAll adapters carry unique serials and can be pinned.")
    print("Record which device each is plugged into, then put them in")
    print(f"{RACK_CONFIG} and run: ccnabot rack udev")
    return 0


def cmd_rack_udev(args) -> int:
    rack = load_local_rack()
    if not rack.cabled:
        print(f"No console adapters registered for {rack.id}.", file=sys.stderr)
        print(f"Run 'ccnabot rack discover', then list the serials in "
              f"{RACK_CONFIG}.", file=sys.stderr)
        return 1
    text = udev_mod.rules_for(rack, group=args.group)
    if args.output:
        pathlib.Path(args.output).write_text(text)
        print(f"wrote {args.output} ({len(rack.cabled)} device(s))")
    else:
        print(text, end="")
    return 0


# -- drill ---------------------------------------------------------------

def cmd_drill(args) -> int:
    student = whoami(args)
    difficulty = subnetting.Difficulty[args.difficulty.upper()]
    drills = subnetting.generate_set(args.count, difficulty=difficulty, seed=args.seed)

    print(f"Subnetting practice - task 504 - {len(drills)} questions "
          f"({difficulty.name.lower()})")
    print("Enter '?' for a hint, 's' to skip, 'q' to quit.\n")

    correct = 0
    with open_log(args) as log:
        for n, drill in enumerate(drills, 1):
            print(f"[{n}/{len(drills)}] {drill.prompt}")
            hints, outcome = 0, None

            while outcome is None:
                try:
                    reply = input("  > ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nStopped.")
                    return 0

                if reply.lower() == "q":
                    print(f"\nStopped. {correct}/{n - 1} correct.")
                    return 0
                if reply.lower() == "s":
                    print(f"  Answer: {drill.answer}\n")
                    outcome = Outcome.INCORRECT
                    break
                if reply == "?":
                    hints += 1
                    hint = drill.hint(hints)
                    if hints >= drill.hint_levels:
                        print(f"  {hint}\n")
                        outcome = Outcome.INCORRECT
                        break
                    print(f"  hint {hints}/{drill.hint_levels - 1}: {hint}")
                    continue

                ok, missed = drill.check(reply)
                if ok:
                    # Getting there unaided is a stronger signal than getting
                    # there after three hints, and FSRS should see the difference.
                    outcome = Outcome.CORRECT if hints == 0 else Outcome.PARTIAL
                    correct += 1
                    print("  correct" + (f" (after {hints} hint(s))" if hints else "") + "\n")
                elif missed is not None:
                    # Name the misconception rather than just marking it wrong.
                    print(f"  not quite - {missed.explanation}")
                else:
                    print("  not quite. Try again, or '?' for a hint.")

            log.record(
                student, drill.task_id, EventKind.DRILL, outcome,
                kind=drill.kind.value, difficulty=difficulty.name,
                hints=hints, seed=drill.seed,
            )

    print(f"{correct}/{len(drills)} correct. Recorded against task 504.")
    return 0


# -- review --------------------------------------------------------------

def cmd_review(args) -> int:
    student, today = whoami(args), args.today or date.today()
    cat, cal = catalog(), calendar()
    sched = RetentionScheduler(cat, cal)

    with open_log(args) as log:
        observations = log.observations(student)

    queue = sched.queue(observations, today=today, limit=args.count, student_id=student)
    if not queue:
        print(f"Nothing due for {student} today.")
        return 0

    days = cal.days_until_exam(Exam.WRITTEN, today)
    print(f"Review queue for {student} - {today.isoformat()}"
          + (f" ({days} days to NOCTI written)" if days and days > 0 else ""))
    print(f"\n{'task':<7}{'section':<34}{'recall':>8}  {'status':<12}why")
    print("-" * 92)
    for item in queue:
        task = cat[item.task_id]
        section = cat.sections[task.section][:32]
        why = "not retaught this year" if item.never_retaught else "in this year's plan"
        recall = "-" if item.reviews == 0 else f"{item.retrievability * 100:.0f}%"
        print(f"{item.task_id:<7}{section:<34}{recall:>8}  "
              f"{item.urgency.value:<12}{why}")
    print(f"\n{len(queue)} item(s). Run 'ccnabot drill' for task 504 practice.")
    return 0


# -- attest --------------------------------------------------------------

def cmd_attest(args) -> int:
    cat = catalog()
    if args.task not in cat:
        print(f"error: {args.task} is not an assessable task in CIP {report_mod.CIP}",
              file=sys.stderr)
        return 2

    task = cat[args.task]
    outcome = Outcome.REVOKED if args.revoke else Outcome.ATTESTED
    with open_log(args) as log:
        log.record(
            args.student, args.task, EventKind.ATTESTATION, outcome,
            instructor=args.instructor or getpass.getuser(),
        )
    verb = "revoked" if args.revoke else "attested"
    print(f"{verb}: {args.student} - {task.id} {task.title}")
    return 0


# -- report --------------------------------------------------------------

def cmd_report(args) -> int:
    student = whoami(args)
    cat = catalog()
    with open_log(args) as log:
        observations = log.observations(student)
        names = {r["student_id"]: r["display_name"] for r in log.students(active_only=False)}

    record = LearnerRecord.build(student, observations, cat)

    if args.csv:
        print(report_mod.to_csv(record, cat), end="")
        return 0

    print(report_mod.render(
        record, cat,
        display_name=names.get(student, ""),
        options=report_mod.ReportOptions(
            show_evidence=args.evidence,
            show_untouched=not args.outstanding_only,
            today=args.today,
        ),
    ))

    if args.outstanding_only:
        groups = report_mod.outstanding_by_modality(record, cat)
        needs = groups.get(Modality.ATTEST, [])
        if needs:
            print(f"\n{len(needs)} outstanding task(s) can only be marked by an "
                  f"instructor:\n  {', '.join(needs)}")
    return 0


# -- wiring --------------------------------------------------------------

def _iso(value: str) -> date:
    return date.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccnabot", description=__doc__.splitlines()[0])
    p.add_argument("--db", help="observation store (default: XDG data dir)")
    p.add_argument("--student", help="student id (default: POSIX username)")
    p.add_argument("--today", type=_iso, help="override today's date (YYYY-MM-DD)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="where the class is in the year")
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("drill", help="subnetting practice (task 504)")
    d.add_argument("-n", "--count", type=int, default=8)
    d.add_argument("--difficulty", default="within",
                   choices=[x.name.lower() for x in subnetting.Difficulty])
    d.add_argument("--seed", type=int, help="reproduce an exact practice set")
    d.set_defaults(func=cmd_drill)

    r = sub.add_parser("review", help="what to revise today")
    r.add_argument("-n", "--count", type=int, default=12)
    r.set_defaults(func=cmd_review)

    a = sub.add_parser("attest", help="instructor: mark a task proficient")
    a.add_argument("--student", required=True)
    a.add_argument("--task", required=True)
    a.add_argument("--instructor")
    a.add_argument("--revoke", action="store_true")
    a.set_defaults(func=cmd_attest)

    c = sub.add_parser("console", help="attach to a device console")
    c.add_argument("device", help="device name, e.g. r1 or sw2")
    c.add_argument("--no-record", action="store_true", help="do not save a transcript")
    c.add_argument("--terminal", choices=[t.name for t in console_mod.KNOWN_TERMINALS])
    c.set_defaults(func=cmd_console)

    rk = sub.add_parser("rack", help="rack setup and status")
    rksub = rk.add_subparsers(dest="rack_command", required=True)

    rs = rksub.add_parser("status", help="devices, consoles and who is on them")
    rs.set_defaults(func=cmd_rack_status)

    rd = rksub.add_parser("discover", help="list attached USB-serial adapters")
    rd.set_defaults(func=cmd_rack_discover)

    ru = rksub.add_parser("udev", help="generate console pinning rules")
    ru.add_argument("-o", "--output")
    ru.add_argument("--group", default="dialout")
    ru.set_defaults(func=cmd_rack_udev)

    rep = sub.add_parser("report", help="the signed competency task list")
    rep.add_argument("--evidence", action="store_true", help="show what each mark rests on")
    rep.add_argument("--outstanding-only", action="store_true")
    rep.add_argument("--csv", action="store_true")
    rep.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Subcommand-level --student wins over the global one.
    if getattr(args, "student", None) is None:
        args.student = None
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
