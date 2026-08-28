"""Focused tests for schedule interpretation."""
from datetime import date

from custom_components.periodical.sensor import (
    _day_is_absence,
    _day_is_working,
    _day_hours,
    _split_hours,
)


def test_absence_overrides_attached_rotation_shift() -> None:
    day = {
        "date": date.today().isoformat(),
        "status": "vacation",
        "shift": {"code": "N2", "start_time": "14:00", "end_time": "22:30"},
    }
    assert _day_is_absence(day)
    assert not _day_is_working(day)


def test_overnight_hours() -> None:
    day = {
        "status": "working",
        "shift": {"code": "N3", "start_time": "22:00", "end_time": "06:30"},
    }
    assert _day_hours(day, {}) == 8.5


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
    assert _split_hours(days, {}) == (0.0, 24.0)
