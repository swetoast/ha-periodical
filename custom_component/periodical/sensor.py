"""Sensor platform for Periodical."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ABSENCE_STATUSES,
    DATA_ABSENCES,
    DATA_ME,
    DATA_NEXT_SHIFT,
    DATA_PAY_MONTH,
    DATA_SCHEDULE_MONTH,
    DATA_SCHEDULE_WEEK,
    DATA_SCHEDULE_WINDOW,
    DATA_SCHEDULE_YEAR,
    DATA_SHIFTS,
    DATA_STATUS,
    DATA_VACATION_BALANCE,
    DOMAIN,
    ONCALL_SHIFT_CODES,
    STATUS_WORKING,
)
from .coordinator import PeriodicalCoordinator
from .entity import PeriodicalEntity, async_cleanup_registry

_LOGGER = logging.getLogger(__name__)

OB_CODES = ("OB1", "OB2", "OB3", "OB4", "OB5")


@dataclass(frozen=True, kw_only=True)
class PeriodicalSensorDescription(SensorEntityDescription):
    """Describe a Periodical sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
def _hhmm_to_datetime(val: str | None, base_date: date | None = None) -> datetime | None:
    """Convert 'HH:MM' to a tz-aware datetime in HA's local zone (DST-correct)."""
    if not val:
        return None
    try:
        hour, minute = (int(part) for part in val.split(":")[:2])
        base = base_date or dt_util.now().date()
        return datetime.combine(base, time(hour, minute), tzinfo=dt_util.DEFAULT_TIME_ZONE)
    except (IndexError, ValueError, TypeError):
        return None


def _parse_iso_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _get_day_list(block: Any) -> list[dict]:
    """Day objects out of any /schedule/* response.

    Every schedule endpoint in the published API returns either a bare list or
    {"days": [...]}; no other shape is probed.
    """
    if isinstance(block, list):
        return [d for d in block if isinstance(d, dict)]
    if isinstance(block, dict):
        days = block.get("days")
        if isinstance(days, list):
            return [d for d in days if isinstance(d, dict)]
    return []


def _shift_index(data: dict) -> dict[str, dict]:
    """Shift code -> canonical definition from the /shifts catalog."""
    defs = data.get(DATA_SHIFTS)
    if isinstance(defs, list):
        return {d["code"]: d for d in defs if isinstance(d, dict) and d.get("code")}
    return {}


def _day_status(day: Any) -> str:
    if not isinstance(day, dict):
        return ""
    status = day.get("status")
    return status.lower() if isinstance(status, str) else ""


def _day_is_working(day: Any) -> bool:
    """True only when the person actually works that day.

    A vacation / sick / VAB / leave day still carries its rotation shift in the
    payload, so the shift block alone must never be treated as proof of work.
    """
    return _day_status(day) == STATUS_WORKING


def _day_is_absence(day: Any) -> bool:
    return _day_status(day) in ABSENCE_STATUSES


def _working_shift(day: Any) -> dict | None:
    """The shift block for a day, but only if that day is actually worked."""
    if not _day_is_working(day):
        return None
    shift = day.get("shift")
    if isinstance(shift, dict) and shift.get("start_time"):
        return shift
    return None


def _is_oncall(shift: Any) -> bool:
    return isinstance(shift, dict) and shift.get("code") in ONCALL_SHIFT_CODES


def _shift_overnight(shift: dict, index: dict[str, dict]) -> bool:
    overnight = shift.get("overnight")
    if overnight is None:
        overnight = index.get(shift.get("code") or "", {}).get("overnight")
    return bool(overnight)


def _shift_end_dt(shift: dict, base: date | None, index: dict[str, dict]) -> datetime | None:
    """End timestamp for a shift starting on `base`, rolling past midnight."""
    start_dt = _hhmm_to_datetime(shift.get("start_time"), base)
    end_dt = _hhmm_to_datetime(shift.get("end_time"), base)
    if end_dt is None or start_dt is None:
        return end_dt
    # The clock wrapping past midnight, or equal start/end on an overnight shift
    # (on-call 00:00 -> 00:00 = 24 h), both mean the end belongs to the next day.
    if end_dt < start_dt or (end_dt == start_dt and _shift_overnight(shift, index)):
        end_dt += timedelta(days=1)
    return end_dt


def _day_hours(day: dict, index: dict[str, dict]) -> float:
    """Hours spanned by a day's shift, from explicit total or start/end."""
    total = day.get("total_hours")
    if total is not None:
        try:
            return round(float(total), 2)
        except (TypeError, ValueError):
            pass
    shift = day.get("shift")
    if not isinstance(shift, dict):
        return 0.0
    canon = index.get(shift.get("code") or "", {})
    start_str = shift.get("start_time") or canon.get("start_time")
    end_str = shift.get("end_time") or canon.get("end_time")
    if not start_str or not end_str:
        return 0.0
    try:
        sh, sm = (int(p) for p in start_str.split(":")[:2])
        eh, em = (int(p) for p in end_str.split(":")[:2])
    except (IndexError, ValueError):
        return 0.0
    duration = (eh * 60 + em) - (sh * 60 + sm)
    if duration < 0:
        duration += 24 * 60
    elif duration == 0:
        duration = 24 * 60 if _shift_overnight(shift, index) else 0
    return round(duration / 60, 2)


def _split_hours(days: list[dict], index: dict[str, dict]) -> tuple[float, float]:
    """(worked_hours, oncall_hours) over a set of days.

    On-call is a 24 h stand-by block, not 24 h of work; payroll reports it as a
    separate oncall_hours figure, so it is kept out of the worked total.
    """
    worked = oncall = 0.0
    for day in days:
        if not _day_is_working(day):
            continue
        hours = _day_hours(day, index)
        if _is_oncall(day.get("shift")):
            oncall += hours
        else:
            worked += hours
    return round(worked, 2), round(oncall, 2)


# ---------------------------------------------------------------------------
# Today / yesterday / tomorrow
# ---------------------------------------------------------------------------
def _window_days(data: dict) -> dict[str, dict]:
    """date string -> day object from the yesterday..tomorrow range call."""
    return {
        day["date"]: day
        for day in _get_day_list(data.get(DATA_SCHEDULE_WINDOW))
        if day.get("date")
    }


def _day_at_offset(data: dict, offset: int) -> dict | None:
    target = (dt_util.now().date() + timedelta(days=offset)).isoformat()
    return _window_days(data).get(target)


def _today_day(data: dict) -> dict | None:
    """Today's day object: /status first, the range window as fallback."""
    status = data.get(DATA_STATUS)
    if isinstance(status, dict) and status.get("status") is not None:
        return status
    return _day_at_offset(data, 0)


def _active_shift_and_date(data: dict) -> tuple[dict | None, date | None]:
    """The shift that is relevant right now, and the date it started on.

    A night shift that began at 22:00 yesterday is still the active one at 02:00
    today, so yesterday is checked first and only accepted while its end is still
    in the future.  Days the person is absent on never produce a shift, which is
    what keeps a vacation day from reporting its rotation shift's hours.
    """
    index = _shift_index(data)
    now = dt_util.now()

    yesterday = _day_at_offset(data, -1)
    yesterday_shift = _working_shift(yesterday)
    if yesterday_shift is not None:
        base = _parse_iso_date(yesterday.get("date"))
        end = _shift_end_dt(yesterday_shift, base, index)
        if end is not None and now < end:
            return yesterday_shift, base

    today = _today_day(data)
    today_shift = _working_shift(today)
    if today_shift is not None:
        return today_shift, _parse_iso_date(today.get("date")) or now.date()

    return None, None


def _today_start(data: dict) -> datetime | None:
    shift, base = _active_shift_and_date(data)
    if shift is None:
        return None
    return _hhmm_to_datetime(shift.get("start_time"), base)


def _today_end(data: dict) -> datetime | None:
    shift, base = _active_shift_and_date(data)
    if shift is None:
        return None
    return _shift_end_dt(shift, base, _shift_index(data))


def _today_shift_attrs(data: dict) -> dict[str, Any]:
    """Live shift detail, plus the rotation shift the day would otherwise have.

    On an absence day the scheduled_* keys still show what the rotation says,
    while the shift_* keys stay empty because nothing is actually being worked.
    """
    day = _today_day(data)
    if not isinstance(day, dict):
        return {}

    attrs: dict[str, Any] = {
        "status": day.get("status"),
        "absence": _day_is_absence(day),
    }

    scheduled = day.get("shift")
    if isinstance(scheduled, dict):
        attrs["scheduled_shift_code"] = scheduled.get("code")
        attrs["scheduled_shift_label"] = scheduled.get("label")
        attrs["scheduled_start_time"] = scheduled.get("start_time")
        attrs["scheduled_end_time"] = scheduled.get("end_time")

    shift, base = _active_shift_and_date(data)
    if shift is not None:
        attrs.update(
            {
                "shift_code": shift.get("code"),
                "shift_label": shift.get("label"),
                "shift_color": shift.get("color"),
                "start_time": shift.get("start_time"),
                "end_time": shift.get("end_time"),
                "overnight": _shift_overnight(shift, _shift_index(data)),
                "shift_date": base.isoformat() if base else None,
                "on_call": _is_oncall(shift),
            }
        )

    return {k: v for k, v in attrs.items() if v is not None}


def _get_coworkers(data: dict) -> list[dict]:
    day = _today_day(data)
    if isinstance(day, dict):
        coworkers = day.get("coworkers")
        if isinstance(coworkers, list):
            return [c for c in coworkers if isinstance(c, dict)]
    return []


def _today_coworkers_count(data: dict) -> int:
    return len(_get_coworkers(data))


def _today_coworkers_attrs(data: dict) -> dict[str, Any]:
    return {
        "co_workers": [
            {
                "name": cw.get("name"),
                "shift_code": cw.get("shift_code"),
                "shift_label": cw.get("shift_label"),
            }
            for cw in _get_coworkers(data)
        ]
    }


def _status_today(data: dict) -> str | None:
    day = _today_day(data)
    return day.get("status") if isinstance(day, dict) else None


def _status_attrs(data: dict) -> dict[str, Any]:
    """Day context that has no sensor of its own."""
    day = _today_day(data)
    if not isinstance(day, dict):
        return {}
    attrs: dict[str, Any] = {
        "working": _day_is_working(day),
        "absence": _day_is_absence(day),
        "date": day.get("date"),
    }
    for key in ("overtime", "partial_day", "ob_pay"):
        if day.get(key) is not None:
            attrs[key] = day[key]
    return attrs


def _ob_total(data: dict) -> float | None:
    day = _today_day(data)
    if not isinstance(day, dict):
        return None
    val = day.get("ob_total")
    try:
        return round(float(val), 2) if val is not None else None
    except (TypeError, ValueError):
        return None


def _rotation_week(data: dict) -> int | None:
    day = _today_day(data)
    if not isinstance(day, dict):
        return None
    try:
        val = day.get("rotation_week")
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _tomorrow_day(data: dict) -> dict | None:
    return _day_at_offset(data, 1)


def _tomorrow_shift_date(data: dict) -> date | None:
    """Tomorrow's date, but only when tomorrow is actually a working day."""
    day = _tomorrow_day(data)
    if _working_shift(day) is None:
        return None
    return _parse_iso_date(day.get("date"))


def _tomorrow_shift_start(data: dict) -> str | None:
    shift = _working_shift(_tomorrow_day(data))
    return shift.get("start_time") if shift else None


def _tomorrow_shift_end(data: dict) -> str | None:
    shift = _working_shift(_tomorrow_day(data))
    return shift.get("end_time") if shift else None


def _tomorrow_shift_attrs(data: dict) -> dict[str, Any]:
    day = _tomorrow_day(data)
    if not isinstance(day, dict):
        return {}
    attrs: dict[str, Any] = {
        "date": day.get("date"),
        "status": day.get("status"),
        "absence": _day_is_absence(day),
        "working": _day_is_working(day),
        "rotation_week": day.get("rotation_week"),
    }
    scheduled = day.get("shift")
    if isinstance(scheduled, dict):
        attrs["scheduled_shift_code"] = scheduled.get("code")
        attrs["scheduled_shift_label"] = scheduled.get("label")
    shift = _working_shift(day)
    if shift is not None:
        attrs["shift_code"] = shift.get("code")
        attrs["shift_label"] = shift.get("label")
        attrs["shift_color"] = shift.get("color")
        attrs["on_call"] = _is_oncall(shift)
    return {k: v for k, v in attrs.items() if v is not None}


# ---------------------------------------------------------------------------
# Week / month / year aggregates
# ---------------------------------------------------------------------------
def _week_shifts_count(data: dict) -> int | None:
    block = data.get(DATA_SCHEDULE_WEEK)
    if block is None:
        return None
    return sum(1 for day in _get_day_list(block) if _day_is_working(day))


def _week_hours(data: dict) -> float | None:
    block = data.get(DATA_SCHEDULE_WEEK)
    if block is None:
        return None
    worked, _ = _split_hours(_get_day_list(block), _shift_index(data))
    return worked


def _week_attrs(data: dict) -> dict[str, Any]:
    """Full day-by-day breakdown; carried by Shifts This Week only."""
    block = data.get(DATA_SCHEDULE_WEEK)
    if block is None:
        return {}
    days = []
    for day in _get_day_list(block):
        shift = day.get("shift") if isinstance(day.get("shift"), dict) else {}
        days.append(
            {
                "date": day.get("date"),
                "status": day.get("status"),
                "working": _day_is_working(day),
                "absence": _day_is_absence(day),
                "shift_code": shift.get("code"),
                "shift_label": shift.get("label"),
                "start_time": shift.get("start_time"),
                "end_time": shift.get("end_time"),
            }
        )
    absence_days = sum(1 for d in days if d["absence"])
    return {"days": days, "absence_days": absence_days}


def _week_hours_attrs(data: dict) -> dict[str, Any]:
    block = data.get(DATA_SCHEDULE_WEEK)
    if block is None:
        return {}
    worked, oncall = _split_hours(_get_day_list(block), _shift_index(data))
    return {
        "worked_hours": worked,
        "oncall_hours": oncall,
        "total_hours_including_oncall": round(worked + oncall, 2),
    }


def _year_days(data: dict) -> list[dict] | None:
    block = data.get(DATA_SCHEDULE_YEAR)
    if block is None:
        return None
    return _get_day_list(block)


def _year_total_shifts(data: dict) -> int | None:
    days = _year_days(data)
    return None if days is None else sum(1 for day in days if _day_is_working(day))


def _year_remaining_shifts(data: dict) -> int | None:
    days = _year_days(data)
    if days is None:
        return None
    today = dt_util.now().date().isoformat()
    return sum(1 for day in days if _day_is_working(day) and (day.get("date") or "") >= today)


def _year_total_hours(data: dict) -> float | None:
    days = _year_days(data)
    if days is None:
        return None
    worked, _ = _split_hours(days, _shift_index(data))
    return worked


def _year_hours_attrs(data: dict) -> dict[str, Any]:
    days = _year_days(data)
    if days is None:
        return {}
    worked, oncall = _split_hours(days, _shift_index(data))
    return {
        "worked_hours": worked,
        "oncall_hours": oncall,
        "total_hours_including_oncall": round(worked + oncall, 2),
        "vacation_days": sum(1 for d in days if _day_status(d) == "vacation"),
        "absence_days": sum(1 for d in days if _day_is_absence(d)),
    }


def _schedule_month_working_days(data: dict) -> int | None:
    block = data.get(DATA_SCHEDULE_MONTH)
    if block is None:
        return None
    return sum(1 for day in _get_day_list(block) if _day_is_working(day))


def _schedule_month_attrs(data: dict) -> dict[str, Any]:
    block = data.get(DATA_SCHEDULE_MONTH)
    if not isinstance(block, dict):
        return {}
    days = _get_day_list(block)
    worked, oncall = _split_hours(days, _shift_index(data))
    attrs: dict[str, Any] = {
        "worked_hours": worked,
        "oncall_hours": oncall,
        "absence_days": sum(1 for d in days if _day_is_absence(d)),
        "vacation_days": sum(1 for d in days if _day_status(d) == "vacation"),
        "off_days": sum(1 for d in days if _day_status(d) == "off"),
    }
    for key in ("month", "year", "ob_total", "wage"):
        if block.get(key) is not None:
            attrs[key] = block[key]
    return attrs


# ---------------------------------------------------------------------------
# Next shift
# ---------------------------------------------------------------------------
def _next_shift(data: dict) -> dict:
    block = data.get(DATA_NEXT_SHIFT)
    return block if isinstance(block, dict) else {}


def _next_shift_date(data: dict) -> date | None:
    return _parse_iso_date(_next_shift(data).get("date"))


def _next_shift_start(data: dict) -> str | None:
    shift = _next_shift(data).get("shift")
    return shift.get("start_time") if isinstance(shift, dict) else None


def _next_shift_end(data: dict) -> str | None:
    shift = _next_shift(data).get("shift")
    return shift.get("end_time") if isinstance(shift, dict) else None


def _next_shift_attrs(data: dict) -> dict[str, Any]:
    block = _next_shift(data)
    attrs: dict[str, Any] = {
        "days_from_today": block.get("days_from_today"),
        "rotation_week": block.get("rotation_week"),
    }
    shift = block.get("shift")
    if isinstance(shift, dict):
        attrs.update(
            {
                "shift_code": shift.get("code"),
                "shift_label": shift.get("label"),
                "shift_color": shift.get("color"),
                "start_time": shift.get("start_time"),
                "end_time": shift.get("end_time"),
                "overnight": shift.get("overnight"),
                "on_call": _is_oncall(shift),
            }
        )
    return {k: v for k, v in attrs.items() if v is not None}


# ---------------------------------------------------------------------------
# Vacation
# ---------------------------------------------------------------------------
def _vacation_field(data: dict, key: str) -> Any:
    block = data.get(DATA_VACATION_BALANCE)
    return block.get(key) if isinstance(block, dict) else None


def _vacation_remaining(data: dict) -> float | None:
    return _vacation_field(data, "remaining_days")


def _vacation_used(data: dict) -> float | None:
    return _vacation_field(data, "used_days")


def _vacation_total(data: dict) -> float | None:
    # total_available includes days saved from the previous year; fall back to
    # the base entitlement if the API ever omits it.
    block = data.get(DATA_VACATION_BALANCE)
    if not isinstance(block, dict):
        return None
    total = block.get("total_available")
    return block.get("entitled_days") if total is None else total


def _vacation_attrs(data: dict) -> dict[str, Any]:
    block = data.get(DATA_VACATION_BALANCE)
    if not isinstance(block, dict):
        return {}
    keep = (
        "year",
        "year_start",
        "year_end",
        "entitled_days",
        "saved_from_previous",
        "total_available",
        "used_days",
        "is_first_year",
        "projection",
    )
    attrs = {k: block[k] for k in keep if block.get(k) is not None}
    # Days currently booked as vacation in the fetched year, for a sanity check
    # against the payroll figure.
    days = _year_days(data)
    if days is not None:
        attrs["vacation_days_scheduled"] = sum(1 for d in days if _day_status(d) == "vacation")
    return attrs


# ---------------------------------------------------------------------------
# Pay
# ---------------------------------------------------------------------------
def _pay_block(data: dict) -> dict:
    block = data.get(DATA_PAY_MONTH)
    return block if isinstance(block, dict) else {}


def _pay_float(data: dict, key: str) -> float | None:
    val = _pay_block(data).get(key)
    if val is None:
        return None
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return None


def _pay_int(data: dict, key: str) -> int | None:
    val = _pay_block(data).get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _pay_ob(data: dict, field: str, code: str) -> float | None:
    bucket = _pay_block(data).get(field)
    if not isinstance(bucket, dict):
        return None
    val = bucket.get(code)
    try:
        return round(float(val), 2) if val is not None else None
    except (TypeError, ValueError):
        return None


def _sum_dict(data: dict, field: str) -> float | None:
    bucket = _pay_block(data).get(field)
    if not isinstance(bucket, dict):
        return None
    total = 0.0
    for val in bucket.values():
        try:
            total += float(val)
        except (TypeError, ValueError):
            pass
    return round(total, 2)


def _pay_attrs(data: dict) -> dict[str, Any]:
    """Everything from /pay/month that has no sensor of its own."""
    block = _pay_block(data)
    if not block:
        return {}
    keep = (
        "year",
        "month",
        "brutto_pay",
        "base_salary",
        "wage_type",
        "tax_table",
        "ob_pay",
        "ob_hours",
        "absence_deduction",
        "absence_hours",
        "vab_hours",
        "leave_hours",
        "parental_days",
        "parental_hours",
        "off_days",
        "off_hours",
        "vacation_days",
        "substitute_hours",
        "substitute_base_pay",
    )
    attrs = {k: block[k] for k in keep if block.get(k) is not None}
    attrs["ob_total_pay"] = _sum_dict(data, "ob_pay")
    return {k: v for k, v in attrs.items() if v is not None}


def _ob_summary_attrs(data: dict) -> dict[str, Any]:
    if not _pay_block(data):
        return {}
    attrs: dict[str, Any] = {"total_hours": _sum_dict(data, "ob_hours")}
    for code in OB_CODES:
        attrs[f"{code.lower()}_pay"] = _pay_ob(data, "ob_pay", code)
        attrs[f"{code.lower()}_hours"] = _pay_ob(data, "ob_hours", code)
    return {k: v for k, v in attrs.items() if v is not None}


def _sick_ob_summary_attrs(data: dict) -> dict[str, Any]:
    if not _pay_block(data):
        return {}
    attrs: dict[str, Any] = {
        "lost": _pay_float(data, "sick_ob_lost"),
        "total_hours": _sum_dict(data, "sick_ob_hours_by_code"),
    }
    for code in OB_CODES:
        attrs[f"{code.lower()}_pay"] = _pay_ob(data, "sick_ob_pay_by_code", code)
        attrs[f"{code.lower()}_hours"] = _pay_ob(data, "sick_ob_hours_by_code", code)
    return {k: v for k, v in attrs.items() if v is not None}


def _absence_summary_attrs(data: dict) -> dict[str, Any]:
    block = _pay_block(data)
    if not block:
        return {}
    keys = (
        "absence_hours",
        "sick_days",
        "sick_hours",
        "vab_days",
        "vab_hours",
        "leave_days",
        "leave_hours",
        "parental_days",
        "parental_hours",
        "vacation_days",
    )
    return {k: block[k] for k in keys if block.get(k) is not None}


# ---------------------------------------------------------------------------
# Absences / account
# ---------------------------------------------------------------------------
def _absence_items(data: dict) -> list[dict]:
    block = data.get(DATA_ABSENCES)
    if isinstance(block, list):
        return [item for item in block if isinstance(item, dict)]
    if isinstance(block, dict):
        items = block.get("absences") or block.get("items") or []
        return [item for item in items if isinstance(item, dict)]
    return []


def _absences_count(data: dict) -> int | None:
    return None if data.get(DATA_ABSENCES) is None else len(_absence_items(data))


def _absences_attrs(data: dict) -> dict[str, Any]:
    return {"absences": _absence_items(data)}


def _me_name(data: dict) -> str | None:
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return None
    name = me.get("name") or me.get("username")
    if name:
        return str(name)
    uid = me.get("id")
    return f"User {uid}" if uid is not None else None


def _me_attrs(data: dict) -> dict[str, Any]:
    """Scalar fields from /me (id, username, role, is_active, ...)."""
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return {}
    return {k: v for k, v in me.items() if isinstance(v, (str, int, float, bool))}


# ---------------------------------------------------------------------------
# Descriptions
#
# Names come from translations/en.json via translation_key; no description
# carries a redundant `name=` that Home Assistant would ignore anyway.
# ---------------------------------------------------------------------------
SENSOR_DESCRIPTIONS: tuple[PeriodicalSensorDescription, ...] = (
    PeriodicalSensorDescription(
        key="account",
        translation_key="account",
        icon="mdi:account",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_me_name,
        attr_fn=_me_attrs,
    ),
    PeriodicalSensorDescription(
        key="shift_start_today",
        translation_key="shift_start_today",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_today_start,
        attr_fn=_today_shift_attrs,
    ),
    PeriodicalSensorDescription(
        key="shift_end_today",
        translation_key="shift_end_today",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_today_end,
    ),
    PeriodicalSensorDescription(
        key="coworkers_today",
        translation_key="coworkers_today",
        icon="mdi:account-group",
        native_unit_of_measurement="people",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_today_coworkers_count,
        attr_fn=_today_coworkers_attrs,
    ),
    PeriodicalSensorDescription(
        key="status_today",
        translation_key="status_today",
        icon="mdi:information-outline",
        value_fn=_status_today,
        attr_fn=_status_attrs,
    ),
    PeriodicalSensorDescription(
        key="ob_today",
        translation_key="ob_today",
        icon="mdi:cash-plus",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_ob_total,
    ),
    PeriodicalSensorDescription(
        key="rotation_week",
        translation_key="rotation_week",
        icon="mdi:rotate-right",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_rotation_week,
    ),
    PeriodicalSensorDescription(
        key="shifts_this_week",
        translation_key="shifts_this_week",
        icon="mdi:calendar-week",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_week_shifts_count,
        attr_fn=_week_attrs,
    ),
    PeriodicalSensorDescription(
        key="hours_this_week",
        translation_key="hours_this_week",
        icon="mdi:clock-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_week_hours,
        attr_fn=_week_hours_attrs,
    ),
    PeriodicalSensorDescription(
        key="working_days_month",
        translation_key="working_days_month",
        icon="mdi:calendar-month",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_schedule_month_working_days,
        attr_fn=_schedule_month_attrs,
    ),
    PeriodicalSensorDescription(
        key="shifts_this_year",
        translation_key="shifts_this_year",
        icon="mdi:calendar-blank-multiple",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_total_shifts,
    ),
    PeriodicalSensorDescription(
        key="shifts_remaining_year",
        translation_key="shifts_remaining_year",
        icon="mdi:calendar-arrow-right",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_remaining_shifts,
    ),
    PeriodicalSensorDescription(
        key="hours_this_year",
        translation_key="hours_this_year",
        icon="mdi:clock-check-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_total_hours,
        attr_fn=_year_hours_attrs,
    ),
    PeriodicalSensorDescription(
        key="next_shift_date",
        translation_key="next_shift_date",
        icon="mdi:calendar-arrow-right",
        device_class=SensorDeviceClass.DATE,
        value_fn=_next_shift_date,
        attr_fn=_next_shift_attrs,
    ),
    PeriodicalSensorDescription(
        key="next_shift_start",
        translation_key="next_shift_start",
        icon="mdi:clock-start",
        value_fn=_next_shift_start,
    ),
    PeriodicalSensorDescription(
        key="next_shift_end",
        translation_key="next_shift_end",
        icon="mdi:clock-end",
        value_fn=_next_shift_end,
    ),
    PeriodicalSensorDescription(
        key="tomorrow_shift_date",
        translation_key="tomorrow_shift_date",
        icon="mdi:account-clock",
        device_class=SensorDeviceClass.DATE,
        value_fn=_tomorrow_shift_date,
        attr_fn=_tomorrow_shift_attrs,
    ),
    PeriodicalSensorDescription(
        key="tomorrow_shift_start",
        translation_key="tomorrow_shift_start",
        icon="mdi:clock-start",
        value_fn=_tomorrow_shift_start,
    ),
    PeriodicalSensorDescription(
        key="tomorrow_shift_end",
        translation_key="tomorrow_shift_end",
        icon="mdi:clock-end",
        value_fn=_tomorrow_shift_end,
    ),
    PeriodicalSensorDescription(
        key="vacation_remaining",
        translation_key="vacation_remaining",
        icon="mdi:beach",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_vacation_remaining,
        attr_fn=_vacation_attrs,
    ),
    PeriodicalSensorDescription(
        key="vacation_used",
        translation_key="vacation_used",
        icon="mdi:umbrella-beach",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_vacation_used,
    ),
    PeriodicalSensorDescription(
        key="vacation_total",
        translation_key="vacation_total",
        icon="mdi:calendar-check",
        native_unit_of_measurement="days",
        value_fn=_vacation_total,
    ),
    PeriodicalSensorDescription(
        key="pay_month_gross",
        translation_key="pay_month_gross",
        icon="mdi:currency-usd",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "brutto_pay"),
    ),
    PeriodicalSensorDescription(
        key="pay_month_netto",
        translation_key="pay_month_netto",
        icon="mdi:cash-multiple",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "netto_pay"),
        attr_fn=_pay_attrs,
    ),
    PeriodicalSensorDescription(
        key="pay_month_hours",
        translation_key="pay_month_hours",
        icon="mdi:timer-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "total_hours"),
    ),
    PeriodicalSensorDescription(
        key="pay_month_shifts",
        translation_key="pay_month_shifts",
        icon="mdi:calendar-clock",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_int(data, "num_shifts"),
    ),
    PeriodicalSensorDescription(
        key="pay_oncall_month",
        translation_key="pay_oncall_month",
        icon="mdi:phone-clock",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "oncall_pay"),
    ),
    PeriodicalSensorDescription(
        key="pay_oncall_hours_month",
        translation_key="pay_oncall_hours_month",
        icon="mdi:phone-clock",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "oncall_hours"),
    ),
    PeriodicalSensorDescription(
        key="pay_overtime_month",
        translation_key="pay_overtime_month",
        icon="mdi:timer-plus-outline",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "ot_pay"),
    ),
    PeriodicalSensorDescription(
        key="pay_sick_days_month",
        translation_key="pay_sick_days_month",
        icon="mdi:emoticon-sick-outline",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_int(data, "sick_days"),
    ),
    PeriodicalSensorDescription(
        key="pay_sick_hours_month",
        translation_key="pay_sick_hours_month",
        icon="mdi:emoticon-sick-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "sick_hours"),
    ),
    PeriodicalSensorDescription(
        key="pay_vab_days_month",
        translation_key="pay_vab_days_month",
        icon="mdi:baby-face-outline",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_int(data, "vab_days"),
    ),
    PeriodicalSensorDescription(
        key="pay_leave_days_month",
        translation_key="pay_leave_days_month",
        icon="mdi:calendar-minus",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_int(data, "leave_days"),
    ),
    PeriodicalSensorDescription(
        key="absences_count",
        translation_key="absences_count",
        icon="mdi:calendar-remove",
        native_unit_of_measurement="absences",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_absences_count,
        attr_fn=_absences_attrs,
    ),
    # Family summaries — these have no per-field equivalent, so they are the
    # only place the per-OB-code and per-absence-type figures are exposed.
    PeriodicalSensorDescription(
        key="ob_summary",
        translation_key="ob_summary",
        icon="mdi:cash-plus",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _sum_dict(data, "ob_pay"),
        attr_fn=_ob_summary_attrs,
    ),
    PeriodicalSensorDescription(
        key="sick_ob_summary",
        translation_key="sick_ob_summary",
        icon="mdi:cash",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "sick_total_ob"),
        attr_fn=_sick_ob_summary_attrs,
    ),
    PeriodicalSensorDescription(
        key="absence_summary",
        translation_key="absence_summary",
        icon="mdi:cash-minus",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _pay_float(data, "absence_deduction"),
        attr_fn=_absence_summary_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Periodical sensors."""
    coordinator: PeriodicalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_cleanup_registry(hass, entry, SENSOR_DESCRIPTIONS, "sensor")
    async_add_entities(
        PeriodicalSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class PeriodicalSensor(PeriodicalEntity, SensorEntity):
    """A single Periodical sensor."""

    entity_description: PeriodicalSensorDescription

    def __init__(
        self,
        coordinator: PeriodicalCoordinator,
        entry: ConfigEntry,
        description: PeriodicalSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description, ENTITY_ID_FORMAT)

    @property
    def native_value(self) -> Any:
        return self._call(self.entity_description.value_fn, "value")
