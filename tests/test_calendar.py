"""The calendar decides what the tutor is allowed to ask about."""

from datetime import date

from ccnabot.domain.calendar import Exam


def test_year_shape(calendar):
    assert calendar.year == "2026-2027"
    assert calendar.track == "networking"
    assert calendar.start == date(2026, 8, 20)
    assert calendar.end == date(2027, 5, 28)
    assert len(calendar.weeks) == 41


def test_winter_break_is_not_instructional(calendar):
    assert len(calendar.instructional_weeks) == len(calendar.weeks) - 1


def test_week_lookup(calendar):
    """22 Sep 2026 is Trimester 1, Week 6 -- ports and protocols."""
    week = calendar.week_of(date(2026, 9, 22))
    assert week is not None
    assert week.label == "T1 W6"
    assert set(week.standards) == {"501", "502"}


def test_taught_through_grows_monotonically(calendar):
    early = calendar.taught_through(date(2026, 9, 22))
    later = calendar.taught_through(date(2027, 2, 1))
    assert early <= later
    assert "703" not in early, "switch features are not taught until late October"
    assert "703" in later


def test_806_replaced_805_in_the_crosswalk(calendar):
    scheduled = {t for w in calendar.weeks for t in w.standards}
    assert "805" not in scheduled, "805 is RESERVED and must not appear"
    assert "806" in scheduled
    assert len(calendar.scheduled_for("806")) == 2, "both cited weeks were corrected"


def test_the_retention_gap_is_real(calendar, catalog):
    """The whole reason the retention layer exists: most of the grid is
    examined by NOCTI but never retaught this year."""
    never = calendar.never_taught(c.id for c in catalog)
    assert len(never) == 59
    # Hardware, OS and repair are the bulk of it -- prior-year content.
    assert {"203", "216", "409", "307"} <= set(never)
    # Whereas this year's own content is scheduled.
    assert "703" not in never and "504" not in never


def test_nocti_windows_drive_scheduling(calendar):
    written = calendar.exam(Exam.WRITTEN)
    assert written is not None
    assert written.placeholder, "dates are unconfirmed until the coordinator says so"
    assert calendar.days_until_exam(Exam.WRITTEN, date(2026, 9, 22)) == 209
    assert calendar.days_until_exam(Exam.WRITTEN, date(2027, 5, 1)) < 0


def test_next_exam_is_the_practical_then_the_written(calendar):
    nxt = calendar.next_exam(date(2026, 9, 22))
    assert nxt is not None and nxt.exam is Exam.PRACTICAL
    assert calendar.next_exam(date(2027, 4, 1)).exam is Exam.WRITTEN
    assert calendar.next_exam(date(2027, 5, 20)) is None


def test_ccna_labs_are_mapped_to_weeks(calendar):
    """The rack layer will need to know which lab a student is on."""
    labs = {lab for w in calendar.weeks for lab in w.ccna_labs}
    assert "8-1" in labs, "standard ACL lab"
    week = next(w for w in calendar.weeks if "8-1" in w.ccna_labs)
    assert week.trimester == "2"
