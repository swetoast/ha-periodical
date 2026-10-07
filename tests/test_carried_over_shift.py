"""Overnight shifts carried past midnight, using the API's real /status shape.

The payloads mirror responses captured from the live API on 2026-10-07 with the
`date`/`time` simulation parameters. Co-worker names are anonymised.
"""
from __future__ import annotations

import zoneinfo
from collections.abc import Iterator

import pytest

from homeassistant.util import dt as dt_util

from custom_components.periodical import schedule as sched
from custom_components.periodical.binary_sensor import _is_absent_today
from custom_components.periodical.sensor import _same_shift, _today_end, _today_start

STOCKHOLM = zoneinfo.ZoneInfo("Europe/Stockholm")

N3 = {
    "code": "N3",
    "label": "Nattpass",
    "start_time": "22:00",
    "end_time": "06:30",
    "color": "#3f51b5",
    "overnight": True,
}
OFF = {
    "code": "OFF",
    "label": "Ledig",
    "start_time": None,
    "end_time": None,
    "color": "#888888",
    "overnight": False,
}

# Roster of the day the night shift started: every shift type, not only nights.
NIGHT_ROSTER = [
    {"id": 1, "name": "Colleague A", "shift_code": "N2", "shift_label": "Kvällspass"},
    {"id": 2, "name": "Colleague B", "shift_code": "N1", "shift_label": "Dagpass"},
    {"id": 9, "name": "Colleague C", "shift_code": "N3", "shift_label": "Nattpass"},
    {"id": 13, "name": "Colleague D", "shift_code": "OC", "shift_label": "Beredskap"},
]
DAY_ROSTER = [
    {"id": 6, "name": "Colleague E", "shift_code": "N1", "shift_label": "Dagpass"},
    {"id": 7, "name": "Colleague F", "shift_code": "N2", "shift_label": "Kvällspass"},
]


def _status(day: str, status: str, shift: dict, *, carried: bool) -> dict:
    """A /status body; `currently_active_shift` is omitted when not carried over."""
    body = {
        "date": day,
        "status": status,
        "shift": shift,
        "rotation_week": 7,
        "overtime": None,
        "partial_day": None,
        "ob_pay": None,
        "ob_total": 0.0,
        "coworkers": DAY_ROSTER,
    }
    if carried:
        body["currently_active_shift"] = {
            "date": "2026-09-15",
            "shift": N3,
            "rotation_week": 7,
            "coworkers": NIGHT_ROSTER,
        }
    return body


@pytest.fixture
def stockholm(freezer) -> Iterator:
    """Run in the user's time zone and let each test set the clock."""
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(STOCKHOLM)
    yield freezer
    dt_util.set_default_time_zone(original)


def _at(freezer, stamp: str) -> None:
    freezer.move_to(f"{stamp}+02:00")


def test_shift_anchors_on_the_night_it_started(stockholm) -> None:
    _at(stockholm, "2026-09-16T02:00:00")
    data = {"status": _status("2026-09-16", "off", OFF, carried=True)}

    assert _today_start(data).isoformat() == "2026-09-15T22:00:00+02:00"
    assert _today_end(data).isoformat() == "2026-09-16T06:30:00+02:00"
    active = sched.active_shift(data)
    assert active is not None
    assert active.carried_over


def test_still_working_on_a_day_off(stockholm) -> None:
    _at(stockholm, "2026-09-16T02:00:00")
    data = {"status": _status("2026-09-16", "off", OFF, carried=True)}

    assert sched.is_working_now(data) is True
    assert sched.today_day(data)["status"] == "off"


def test_coworkers_come_from_the_night_shift_roster(stockholm) -> None:
    _at(stockholm, "2026-09-16T02:00:00")
    data = {"status": _status("2026-09-16", "off", OFF, carried=True)}

    assert [p["name"] for p in sched.coworkers(data)] == [p["name"] for p in NIGHT_ROSTER]
    assert _same_shift(data) == ["Colleague C"]


def test_holiday_starts_once_the_shift_ends(stockholm) -> None:
    _at(stockholm, "2026-09-16T02:00:00")
    data = {"status": _status("2026-09-16", "vacation", OFF, carried=True), "absences": []}
    assert _is_absent_today(data) is False

    _at(stockholm, "2026-09-16T07:00:00")
    data = {"status": _status("2026-09-16", "vacation", OFF, carried=False), "absences": []}
    assert _is_absent_today(data) is True
    assert sched.is_working_now(data) is False


def test_cached_status_is_rechecked_against_the_clock(stockholm) -> None:
    """A 06:15 status still read at 06:45 must not keep the shift alive."""
    _at(stockholm, "2026-09-16T06:45:00")
    data = {"status": _status("2026-09-16", "off", OFF, carried=True)}

    assert sched.active_shift(data) is None
    assert _today_start(data) is None
    assert sched.is_working_now(data) is False


def test_status_from_yesterday_is_not_used_as_today(stockholm) -> None:
    """After a failed refresh past midnight the old status must be ignored."""
    _at(stockholm, "2026-09-16T00:20:00")
    stale = _status("2026-09-15", "working", N3, carried=False)
    window = {"days": [_status("2026-09-16", "vacation", OFF, carried=False)]}
    data = {"status": stale, "schedule_window": window}

    assert sched.today_day(data)["status"] == "vacation"
