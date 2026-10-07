"""Focused tests for schedule interpretation."""
from datetime import date

from custom_components.periodical.schedule import (
    day_hours,
    day_is_absence,
    day_is_working,
    tally,
)


def test_absence_overrides_attached_rotation_shift() -> None:
    day = {
        "date": date.today().isoformat(),
        "status": "vacation",
        "shift": {"code": "N2", "start_time": "14:00", "end_time": "22:30"},
    }
    assert day_is_absence(day)
    assert not day_is_working(day)


def test_overnight_hours() -> None:
    day = {
        "status": "working",
        "shift": {"code": "N3", "start_time": "22:00", "end_time": "06:30"},
    }
    assert day_hours(day, {}) == 8.5


def test_oncall_is_separate_from_worked_hours() -> None:
    days = [
        {
            "status": "working",
            "shift": {
                "code": "OC",
                "start_time": "00:00",
                "end_time": "00:00",
                "overnight": True,
            },
        }
    ]
    totals = tally(days, {})
    assert (totals.worked_hours, totals.oncall_hours) == (0.0, 24.0)


def test_oncall_is_not_a_shift_but_call_in_is_overtime() -> None:
    """Shift counts follow payroll: on-call is not a shift, call-ins are overtime."""
    n1 = {"code": "N1", "start_time": "06:00", "end_time": "14:30"}
    oc = {"code": "OC", "start_time": "00:00", "end_time": "00:00", "overnight": True}
    days = [
        {"status": "working", "shift": n1},
        {"status": "working", "shift": n1},
        {"status": "working", "shift": oc},
        {"status": "working", "shift": oc, "overtime": {"hours": 8.5}},
        {"status": "vacation", "shift": n1},
    ]
    totals = tally(days, {})
    assert totals.shifts == 2
    assert totals.worked_hours == 17.0
    assert totals.oncall_days == 2
    assert totals.oncall_hours == 48.0
    assert totals.overtime_hours == 8.5
