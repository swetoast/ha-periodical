"""Reading the Periodical schedule payloads.

Pure helpers over the day and shift objects the API returns, shared by both
entity platforms.  Keeping them here means binary_sensor no longer reaches into
sensor's private names, and the two platforms cannot drift on what "working" or
"absent" means.

The shape they operate on, common to /status and every /schedule/* day entry::

    {"date": "2026-07-25", "status": "vacation",
     "shift": {"code": "N2", "label": "Kvällspass",
               "start_time": "14:00", "end_time": "22:30", "overnight": false},
     "rotation_week": 9, "ob_total": 0, "coworkers": [...]}

/status additionally carries ``currently_active_shift`` while an overnight shift
from the previous day is still running.  It holds that shift with its own
co-workers and outranks the top-level day fields for anything describing what
the user is doing right now.  The payload shape is not pinned down by the
OpenAPI document, so it is read tolerantly: either a day-shaped object with a
nested ``shift`` or a bare shift object, with times as ``HH:MM`` or ISO 8601.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, NamedTuple

from homeassistant.util import dt as dt_util

from .const import (
    ABSENCE_STATUSES,
    DATA_ABSENCES,
    DATA_SCHEDULE_WINDOW,
    DATA_SHIFTS,
    DATA_STATUS,
    ONCALL_SHIFT_CODES,
    STATUS_WORKING,
)

Data = dict[str, Any]
"""The coordinator payload: one key per API endpoint."""

Attrs = dict[str, Any]
"""Extra state attributes for one entity."""

Day = dict[str, Any]
Shift = dict[str, Any]
ShiftIndex = dict[str, Shift]

HOURS_PER_DAY = 24
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = HOURS_PER_DAY * MINUTES_PER_HOUR


# ---------------------------------------------------------------------------
# Parsing primitives
# ---------------------------------------------------------------------------
def parse_iso_date(value: str | None) -> date | None:
    """Parse 'YYYY-MM-DD', returning None rather than raising."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def parse_clock(value: Any) -> tuple[date | None, int | None]:
    """Split a time field into (embedded date, minutes since midnight).

    Accepts 'HH:MM', 'HH:MM:SS' and full ISO 8601 datetimes.  The date part is
    None for a bare clock time.  Unparseable input yields (None, None).
    """
    if not isinstance(value, str) or not value:
        return None, None
    if "T" in value:
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None, None
        if parsed.tzinfo is not None:
            parsed = dt_util.as_local(parsed)
        return parsed.date(), parsed.hour * MINUTES_PER_HOUR + parsed.minute
    try:
        hour, minute = (int(part) for part in value.split(":")[:2])
    except ValueError:
        return None, None
    if not (0 <= hour < HOURS_PER_DAY and 0 <= minute < MINUTES_PER_HOUR):
        return None, None
    return None, hour * MINUTES_PER_HOUR + minute


def hhmm_to_datetime(value: Any, base: date | None = None) -> datetime | None:
    """Combine a time field with a date into a tz-aware local datetime.

    An ISO datetime carries its own date, which wins over `base`.
    """
    embedded, minutes = parse_clock(value)
    if minutes is None:
        return None
    day = embedded or base or dt_util.now().date()
    return datetime.combine(
        day,
        time(*divmod(minutes, MINUTES_PER_HOUR)),
        tzinfo=dt_util.DEFAULT_TIME_ZONE,
    )


def day_list(block: Any) -> list[Day]:
    """Day objects out of any /schedule/* response.

    Every schedule endpoint in the published API returns either a bare list or
    {"days": [...]}; no other shape is probed.
    """
    if isinstance(block, list):
        return [day for day in block if isinstance(day, dict)]
    if isinstance(block, dict) and isinstance(block.get("days"), list):
        return [day for day in block["days"] if isinstance(day, dict)]
    return []


def shift_index(data: Data) -> ShiftIndex:
    """Index the /shifts catalog by shift code."""
    catalog = data.get(DATA_SHIFTS)
    if not isinstance(catalog, list):
        return {}
    return {item["code"]: item for item in catalog if isinstance(item, dict) and item.get("code")}


# ---------------------------------------------------------------------------
# Day classification
# ---------------------------------------------------------------------------
def day_status(day: Any) -> str:
    """Lower-cased status of a day, or '' when there isn't one."""
    if not isinstance(day, dict):
        return ""
    status = day.get("status")
    return status.lower() if isinstance(status, str) else ""


def day_is_working(day: Any) -> bool:
    """True only when the person actually works that day.

    A vacation / sick / VAB / leave day still carries its rotation shift in the
    payload, so the shift block alone must never be treated as proof of work.
    """
    return day_status(day) == STATUS_WORKING


def day_is_absence(day: Any) -> bool:
    """True when the day is booked off as vacation, sickness, VAB or leave."""
    return day_status(day) in ABSENCE_STATUSES


def working_shift(day: Any) -> Shift | None:
    """The shift block for a day, but only if that day is actually worked."""
    if not day_is_working(day):
        return None
    shift = day.get("shift")
    if isinstance(shift, dict) and shift.get("start_time"):
        return shift
    return None


def scheduled_shift(day: Any) -> Shift | None:
    """The rotation shift attached to a day, worked or not."""
    if not isinstance(day, dict):
        return None
    shift = day.get("shift")
    return shift if isinstance(shift, dict) else None


def is_oncall(shift: Any) -> bool:
    """Whether a shift is stand-by rather than worked time."""
    return isinstance(shift, dict) and shift.get("code") in ONCALL_SHIFT_CODES


def overtime_hours(day: Any) -> float:
    """Overtime booked on a day, from its `overtime` block.

    In practice this is mostly a call-in from on-call, which payroll books as
    overtime rather than as a shift.
    """
    if not isinstance(day, dict) or day_is_absence(day):
        return 0.0
    block = day.get("overtime")
    if not isinstance(block, dict):
        return 0.0
    try:
        return round(float(block.get("hours") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def counts_as_shift(day: Any) -> bool:
    """A worked rota shift, as payroll counts it in num_shifts.

    On-call days are status `working` in the API but are not shifts: the June
    2026 payslip books 18 shifts against 22 working days, the difference being
    exactly its four on-call days.
    """
    return day_is_working(day) and not is_oncall(scheduled_shift(day))


# ---------------------------------------------------------------------------
# Shift timing
# ---------------------------------------------------------------------------
def shift_is_overnight(shift: Shift, index: ShiftIndex) -> bool:
    """Overnight flag, falling back to the /shifts catalog when the day omits it."""
    overnight = shift.get("overnight")
    if overnight is None:
        overnight = index.get(shift.get("code") or "", {}).get("overnight")
    return bool(overnight)


def shift_end(shift: Shift, base: date | None, index: ShiftIndex) -> datetime | None:
    """End timestamp for a shift starting on `base`, rolling past midnight."""
    start = hhmm_to_datetime(shift.get("start_time"), base)
    end = hhmm_to_datetime(shift.get("end_time"), base)
    if start is None or end is None:
        return end
    if parse_clock(shift.get("end_time"))[0] is not None:
        return end
    # The clock wrapping past midnight, or equal start/end on an overnight shift
    # (on-call 00:00 -> 00:00 = 24 h), both mean the end belongs to the next day.
    if end < start or (end == start and shift_is_overnight(shift, index)):
        end += timedelta(days=1)
    return end


def minutes_since_midnight(value: Any) -> int | None:
    """Minutes from midnight for a time field, or None if unparseable."""
    return parse_clock(value)[1]


def shift_span_minutes(shift: Shift, index: ShiftIndex) -> int:
    """Minutes a shift covers, treating a wrapped or zero-length span as overnight."""
    canonical = index.get(shift.get("code") or "", {})
    start = minutes_since_midnight(shift.get("start_time") or canonical.get("start_time"))
    end = minutes_since_midnight(shift.get("end_time") or canonical.get("end_time"))
    if start is None or end is None:
        return 0

    span = end - start
    if span < 0:
        return span + MINUTES_PER_DAY
    if span == 0:
        # On-call is written 00:00 -> 00:00, meaning a full 24 h, not nothing.
        return MINUTES_PER_DAY if shift_is_overnight(shift, index) else 0
    return span


def day_hours(day: Day, index: ShiftIndex) -> float:
    """Hours spanned by a day's shift, from an explicit total or from start/end."""
    total = day.get("total_hours")
    if total is not None:
        try:
            return round(float(total), 2)
        except (TypeError, ValueError):
            pass

    shift = scheduled_shift(day)
    if shift is None:
        return 0.0
    return round(shift_span_minutes(shift, index) / 60, 2)


@dataclass(slots=True)
class Tally:
    """Shift and hour totals over a set of days, split the way payroll splits them.

    `shifts` and `worked_hours` line up with the payslip's num_shifts and
    total_hours.  On-call is a 24 h stand-by block and overtime is booked
    separately, so neither is folded into the worked figures.
    """

    shifts: int = 0
    worked_hours: float = 0.0
    oncall_days: int = 0
    oncall_hours: float = 0.0
    overtime_hours: float = 0.0

    def as_attrs(self) -> dict[str, Any]:
        """Attribute view for an hours or shifts sensor."""
        return {
            "shifts": self.shifts,
            "worked_hours": self.worked_hours,
            "oncall_days": self.oncall_days,
            "oncall_hours": self.oncall_hours,
            "overtime_hours": self.overtime_hours,
            "total_hours_including_oncall": round(self.worked_hours + self.oncall_hours, 2),
        }


def tally(days: list[Day], index: ShiftIndex) -> Tally:
    """Count shifts and split hours across a set of days."""
    result = Tally()
    for day in days:
        result.overtime_hours += overtime_hours(day)
        if not day_is_working(day):
            continue
        hours = day_hours(day, index)
        if is_oncall(scheduled_shift(day)):
            result.oncall_days += 1
            result.oncall_hours += hours
        else:
            result.shifts += 1
            result.worked_hours += hours
    result.worked_hours = round(result.worked_hours, 2)
    result.oncall_hours = round(result.oncall_hours, 2)
    result.overtime_hours = round(result.overtime_hours, 2)
    return result


# ---------------------------------------------------------------------------
# Locating today, yesterday and tomorrow
# ---------------------------------------------------------------------------
def window_days(data: Data) -> dict[str, Day]:
    """Map each date string in the window to its day object."""
    return {day["date"]: day for day in day_list(data.get(DATA_SCHEDULE_WINDOW)) if day.get("date")}


def day_at_offset(data: Data, offset: int) -> Day | None:
    """The window day `offset` days from today (-1, 0 or +1)."""
    target = (dt_util.now().date() + timedelta(days=offset)).isoformat()
    return window_days(data).get(target)


def _current_status(data: Data) -> Day | None:
    """The /status payload, but only if it describes today.

    A status fetched before midnight still says "yesterday" until the next
    successful refresh; serving it as today would show the wrong day after a
    failed fetch, so a dated status for another day is ignored.
    """
    status = data.get(DATA_STATUS)
    if not isinstance(status, dict) or status.get("status") is None:
        return None
    stamped = parse_iso_date(status.get("date"))
    if stamped is not None and stamped != dt_util.now().date():
        return None
    return status


def today_day(data: Data) -> Day | None:
    """Today's day object: /status first, the range window as fallback."""
    return _current_status(data) or day_at_offset(data, 0)


def tomorrow_day(data: Data) -> Day | None:
    """Tomorrow's day object from the range window."""
    return day_at_offset(data, 1)


class ActiveShift(NamedTuple):
    """The shift that describes what the user is doing right now."""

    shift: Shift
    start_date: date
    carried_over: bool
    """True when the shift began yesterday and is still running past midnight."""
    coworkers: list[dict[str, Any]] | None
    """Co-workers for this specific shift, or None to use the day's list."""


def _shift_running(shift: Shift, start: date, index: ShiftIndex) -> bool:
    """Whether a shift that started on `start` has not yet ended."""
    end = shift_end(shift, start, index)
    return end is None or dt_util.now() < end


def _unwrap_shift(block: dict[str, Any]) -> Shift | None:
    """The shift inside `currently_active_shift`, nested or bare."""
    nested = block.get("shift")
    shift = nested if isinstance(nested, dict) else block
    return shift if (shift.get("start_time") or shift.get("code")) else None


def _carried_over_start(block: dict[str, Any], shift: Shift) -> date:
    """The day a carried-over shift began; by definition that is yesterday."""
    for key in ("date", "shift_date", "start_date"):
        if (stamped := parse_iso_date(block.get(key))) is not None:
            return stamped
    embedded, _ = parse_clock(shift.get("start_time"))
    return embedded or dt_util.now().date() - timedelta(days=1)


def _roster(people: Any) -> list[dict[str, Any]] | None:
    if not isinstance(people, list):
        return None
    return [person for person in people if isinstance(person, dict)]


def _carried_over_from_status(status: Day | None, index: ShiftIndex) -> ActiveShift | None:
    """Read /status `currently_active_shift`, tolerating either plausible shape."""
    block = status.get("currently_active_shift") if isinstance(status, dict) else None
    if not isinstance(block, dict) or (shift := _unwrap_shift(block)) is None:
        return None
    start = _carried_over_start(block, shift)
    # The server only sets this while the shift runs, but the cached status can
    # be up to one refresh interval old, so the end is re-checked locally.
    if not _shift_running(shift, start, index):
        return None
    return ActiveShift(shift, start, True, _roster(block.get("coworkers")))


def active_shift(data: Data) -> ActiveShift | None:
    """The shift relevant right now, or None when nothing is being worked.

    Precedence follows the API contract: /status `currently_active_shift` first,
    then yesterday's overnight shift from the schedule window (for servers or
    cached payloads without that field), then today's own shift.  Days the
    person is absent on never produce a shift, which is what keeps a vacation
    day from reporting its rotation shift's hours.
    """
    index = shift_index(data)

    carried = _carried_over_from_status(_current_status(data), index)
    if carried is not None:
        return carried

    yesterday = day_at_offset(data, -1)
    shift = working_shift(yesterday)
    if shift is not None:
        start = parse_iso_date(yesterday.get("date"))
        end = shift_end(shift, start, index) if start is not None else None
        if start is not None and end is not None and dt_util.now() < end:
            return ActiveShift(shift, start, True, None)

    today = today_day(data)
    shift = working_shift(today)
    if shift is not None:
        start = parse_iso_date(today.get("date")) or dt_util.now().date()
        return ActiveShift(shift, start, False, None)

    return None


def is_working_now(data: Data) -> bool | None:
    """Whether the user is at work today, as opposed to rostered or on stand-by.

    A night shift carried over from yesterday counts, even on a day that is
    otherwise off or the first day of a holiday.  A pure on-call day does not,
    unless the user was called in (overtime booked on the day).
    """
    active = active_shift(data)
    if active is not None and active.carried_over and not is_oncall(active.shift):
        return True
    day = today_day(data)
    if day is None:
        return None
    if not day_is_working(day):
        return False
    return not is_oncall(scheduled_shift(day)) or overtime_hours(day) > 0


def is_on_call_today(data: Data) -> bool | None:
    """Whether today's rota shift is on-call stand-by."""
    day = today_day(data)
    if day is None:
        return None
    return day_is_working(day) and is_oncall(scheduled_shift(day))


def is_absent_today(data: Data) -> bool | None:
    """Whether today is booked off, ignoring the tail of last night's shift.

    While an overnight shift from yesterday is still running the user is at
    work, so the absence only takes effect once that shift ends.
    """
    day = today_day(data)
    if day is None or not day_is_absence(day):
        return None if day is None else False
    active = active_shift(data)
    return not (active is not None and active.carried_over)


def coworkers(data: Data) -> list[dict[str, Any]]:
    """Colleagues working alongside the user right now.

    During a carried-over night shift these are that shift's own co-workers,
    which the API reports separately from the day's roster.
    """
    active = active_shift(data)
    if active is not None and active.coworkers is not None:
        return active.coworkers
    day = today_day(data)
    if not isinstance(day, dict) or not isinstance(day.get("coworkers"), list):
        return []
    return [person for person in day["coworkers"] if isinstance(person, dict)]


def absence_covers(absence: dict[str, Any], day: str) -> bool:
    """Whether an /absences entry spans the given ISO date.

    Field names vary between the list and wrapped forms, so all the documented
    spellings are accepted; a single-day entry has no end and reuses its start.
    """
    start = absence.get("start_date") or absence.get("from") or absence.get("date") or ""
    end = absence.get("end_date") or absence.get("to") or start
    return bool(start) and start <= day <= end


def absence_items(data: Data) -> list[dict[str, Any]]:
    """Entries from /absences, tolerating both the array and wrapped-object forms."""
    block = data.get(DATA_ABSENCES)
    if isinstance(block, list):
        items: Any = block
    elif isinstance(block, dict):
        items = block.get("absences") or block.get("items") or []
    else:
        return []
    return [item for item in items if isinstance(item, dict)]
