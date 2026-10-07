"""Sensor platform for Periodical."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

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

from . import schedule as sched
from .const import (
    DATA_ABSENCES,
    DATA_ME,
    DATA_NEXT_SHIFT,
    DATA_PAY_MONTH,
    DATA_SCHEDULE_MONTH,
    DATA_SCHEDULE_WEEK,
    DATA_SCHEDULE_YEAR,
    DATA_VACATION_BALANCE,
    DOMAIN,
    OB_CODES,
    STATUS_OFF,
    STATUS_VACATION,
)
from .coordinator import PeriodicalCoordinator
from .entity import PeriodicalEntity, async_cleanup_registry
from .schedule import Attrs, Data

UNIT_SEK = "SEK"
UNIT_HOURS = "h"
UNIT_DAYS = "days"
UNIT_SHIFTS = "shifts"


@dataclass(frozen=True, kw_only=True)
class PeriodicalSensorDescription(SensorEntityDescription):
    """Describe a Periodical sensor."""

    value_fn: Callable[[Data], Any]
    attr_fn: Callable[[Data], Attrs] | None = None


def _prune(attrs: Attrs) -> Attrs:
    """Drop None values so absent fields do not clutter the attribute table."""
    return {key: value for key, value in attrs.items() if value is not None}


def _as_float(value: Any, digits: int = 2) -> float | None:
    """Coerce an API field to a rounded float, or None if it is not numeric."""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce an API field to an int, or None if it is not numeric."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------
def _today_start(data: Data) -> datetime | None:
    active = sched.active_shift(data)
    if active is None:
        return None
    return sched.hhmm_to_datetime(active.shift.get("start_time"), active.start_date)


def _today_end(data: Data) -> datetime | None:
    active = sched.active_shift(data)
    if active is None:
        return None
    return sched.shift_end(active.shift, active.start_date, sched.shift_index(data))


def _today_shift_attrs(data: Data) -> Attrs:
    """Live shift detail, plus the rotation shift the day would otherwise have.

    On an absence day the scheduled_* keys still show what the rotation says,
    while the shift_* keys stay empty because nothing is actually being worked.
    """
    day = sched.today_day(data)
    if day is None:
        return {}

    attrs: Attrs = {"status": day.get("status"), "absence": sched.day_is_absence(day)}

    if (scheduled := sched.scheduled_shift(day)) is not None:
        attrs |= {
            "scheduled_shift_code": scheduled.get("code"),
            "scheduled_shift_label": scheduled.get("label"),
            "scheduled_start_time": scheduled.get("start_time"),
            "scheduled_end_time": scheduled.get("end_time"),
        }

    if (active := sched.active_shift(data)) is not None:
        shift = active.shift
        attrs |= {
            "shift_code": shift.get("code"),
            "shift_label": shift.get("label"),
            "shift_color": shift.get("color"),
            "start_time": shift.get("start_time"),
            "end_time": shift.get("end_time"),
            "overnight": sched.shift_is_overnight(shift, sched.shift_index(data)),
            "shift_date": active.start_date.isoformat(),
            "carried_over": active.carried_over,
            "on_call": sched.is_oncall(shift),
        }

    if overtime := sched.overtime_hours(day):
        attrs["overtime_hours"] = overtime

    return _prune(attrs)


def _coworkers_count(data: Data) -> int:
    return len(sched.coworkers(data))


def _same_shift(data: Data) -> list[str] | None:
    """Names of co-workers on the same shift as the user right now.

    The roster covers everyone scheduled that day, so this narrows it to the
    people actually working alongside you.  Overtime cover is booked as
    `OT-<code>` and counts as the same shift.
    """
    active = sched.active_shift(data)
    code = active.shift.get("code") if active is not None else None
    if not code:
        return None
    wanted = {code, f"OT-{code}"}
    return [p.get("name") for p in sched.coworkers(data) if p.get("shift_code") in wanted]


def _coworkers_attrs(data: Data) -> Attrs:
    active = sched.active_shift(data)
    return {
        "for_carried_over_shift": bool(
            active and active.carried_over and active.coworkers is not None
        ),
        "same_shift": _same_shift(data),
        "co_workers": [
            {
                "name": person.get("name"),
                "shift_code": person.get("shift_code"),
                "shift_label": person.get("shift_label"),
            }
            for person in sched.coworkers(data)
        ]
    }


def _status_today(data: Data) -> str | None:
    day = sched.today_day(data)
    return day.get("status") if day else None


def _status_attrs(data: Data) -> Attrs:
    """Day context that has no sensor of its own."""
    day = sched.today_day(data)
    if day is None:
        return {}
    active = sched.active_shift(data)
    attrs: Attrs = {
        "working": sched.is_working_now(data),
        "on_call": sched.is_on_call_today(data),
        "absence": sched.day_is_absence(day),
        "carried_over_shift": bool(active and active.carried_over),
        "date": day.get("date"),
    }
    attrs |= {key: day[key] for key in ("overtime", "partial_day", "ob_pay") if day.get(key)}
    return attrs


def _ob_today(data: Data) -> float | None:
    day = sched.today_day(data)
    return None if day is None else _as_float(day.get("ob_total"))


def _rotation_week(data: Data) -> int | None:
    day = sched.today_day(data)
    return None if day is None else _as_int(day.get("rotation_week"))


# ---------------------------------------------------------------------------
# Tomorrow
# ---------------------------------------------------------------------------
def _tomorrow_shift_date(data: Data) -> date | None:
    """Tomorrow's date, but only when tomorrow is actually a working day."""
    day = sched.tomorrow_day(data)
    if sched.working_shift(day) is None:
        return None
    return sched.parse_iso_date(day.get("date"))


def _tomorrow_time(field: str) -> Callable[[Data], str | None]:
    def value_fn(data: Data) -> str | None:
        shift = sched.working_shift(sched.tomorrow_day(data))
        return shift.get(field) if shift else None

    value_fn.__name__ = f"_tomorrow_{field}"
    return value_fn


def _tomorrow_shift_attrs(data: Data) -> Attrs:
    day = sched.tomorrow_day(data)
    if day is None:
        return {}
    attrs: Attrs = {
        "date": day.get("date"),
        "status": day.get("status"),
        "absence": sched.day_is_absence(day),
        "working": sched.day_is_working(day),
        "rotation_week": day.get("rotation_week"),
    }
    if (scheduled := sched.scheduled_shift(day)) is not None:
        attrs |= {
            "scheduled_shift_code": scheduled.get("code"),
            "scheduled_shift_label": scheduled.get("label"),
        }
    if (shift := sched.working_shift(day)) is not None:
        attrs |= {
            "shift_code": shift.get("code"),
            "shift_label": shift.get("label"),
            "shift_color": shift.get("color"),
            "on_call": sched.is_oncall(shift),
        }
    return _prune(attrs)


# ---------------------------------------------------------------------------
# Week / month / year aggregates
# ---------------------------------------------------------------------------
def _days_or_none(data: Data, key: str) -> list[dict] | None:
    """Day list for a schedule block, or None when the endpoint has no data yet.

    The distinction matters: an empty week is 0 shifts, a missing week is
    unknown, and collapsing the two turns a genuine zero into an unavailable
    sensor that breaks long-term statistics.
    """
    block = data.get(key)
    return None if block is None else sched.day_list(block)


def _tally(data: Data, key: str) -> sched.Tally | None:
    """Payroll-style totals for one schedule block, or None if it has no data."""
    days = _days_or_none(data, key)
    return None if days is None else sched.tally(days, sched.shift_index(data))


def _count_status(days: list[dict], status: str) -> int:
    return sum(1 for day in days if sched.day_status(day) == status)


def _week_shifts(data: Data) -> int | None:
    totals = _tally(data, DATA_SCHEDULE_WEEK)
    return None if totals is None else totals.shifts


def _week_hours(data: Data) -> float | None:
    totals = _tally(data, DATA_SCHEDULE_WEEK)
    return None if totals is None else totals.worked_hours


def _week_attrs(data: Data) -> Attrs:
    """Full day-by-day breakdown; carried by Shifts This Week only."""
    days = _days_or_none(data, DATA_SCHEDULE_WEEK)
    if days is None:
        return {}
    breakdown = []
    for day in days:
        shift = sched.scheduled_shift(day) or {}
        breakdown.append(
            {
                "date": day.get("date"),
                "status": day.get("status"),
                "working": sched.day_is_working(day),
                "absence": sched.day_is_absence(day),
                "on_call": sched.is_oncall(shift),
                "overtime_hours": sched.overtime_hours(day),
                "shift_code": shift.get("code"),
                "shift_label": shift.get("label"),
                "start_time": shift.get("start_time"),
                "end_time": shift.get("end_time"),
            }
        )
    return {
        "days": breakdown,
        "absence_days": sum(1 for entry in breakdown if entry["absence"]),
    }


def _week_hours_attrs(data: Data) -> Attrs:
    totals = _tally(data, DATA_SCHEDULE_WEEK)
    return {} if totals is None else totals.as_attrs()


def _year_shifts(data: Data) -> int | None:
    totals = _tally(data, DATA_SCHEDULE_YEAR)
    return None if totals is None else totals.shifts


def _year_remaining_shifts(data: Data) -> int | None:
    days = _days_or_none(data, DATA_SCHEDULE_YEAR)
    if days is None:
        return None
    today = dt_util.now().date().isoformat()
    return sum(1 for day in days if sched.counts_as_shift(day) and (day.get("date") or "") >= today)


def _year_hours(data: Data) -> float | None:
    totals = _tally(data, DATA_SCHEDULE_YEAR)
    return None if totals is None else totals.worked_hours


def _year_hours_attrs(data: Data) -> Attrs:
    days = _days_or_none(data, DATA_SCHEDULE_YEAR)
    if days is None:
        return {}
    return sched.tally(days, sched.shift_index(data)).as_attrs() | {
        "vacation_days": _count_status(days, STATUS_VACATION),
        "absence_days": sum(1 for day in days if sched.day_is_absence(day)),
    }


def _month_working_days(data: Data) -> int | None:
    totals = _tally(data, DATA_SCHEDULE_MONTH)
    return None if totals is None else totals.shifts


def _month_attrs(data: Data) -> Attrs:
    block = data.get(DATA_SCHEDULE_MONTH)
    if not isinstance(block, dict):
        return {}
    days = sched.day_list(block)
    attrs: Attrs = sched.tally(days, sched.shift_index(data)).as_attrs() | {
        "absence_days": sum(1 for day in days if sched.day_is_absence(day)),
        "vacation_days": _count_status(days, STATUS_VACATION),
        "off_days": _count_status(days, STATUS_OFF),
    }
    attrs |= {key: block[key] for key in ("month", "year", "ob_total", "wage") if key in block}
    return _prune(attrs)


# ---------------------------------------------------------------------------
# Next shift
# ---------------------------------------------------------------------------
def _next_shift_block(data: Data) -> Data:
    block = data.get(DATA_NEXT_SHIFT)
    return block if isinstance(block, dict) else {}


def _next_shift_date(data: Data) -> date | None:
    return sched.parse_iso_date(_next_shift_block(data).get("date"))


def _next_shift_time(field: str) -> Callable[[Data], str | None]:
    def value_fn(data: Data) -> str | None:
        shift = _next_shift_block(data).get("shift")
        return shift.get(field) if isinstance(shift, dict) else None

    value_fn.__name__ = f"_next_shift_{field}"
    return value_fn


def _next_shift_attrs(data: Data) -> Attrs:
    block = _next_shift_block(data)
    attrs: Attrs = {
        "days_from_today": block.get("days_from_today"),
        "rotation_week": block.get("rotation_week"),
    }
    shift = block.get("shift")
    if isinstance(shift, dict):
        attrs |= {
            "shift_code": shift.get("code"),
            "shift_label": shift.get("label"),
            "shift_color": shift.get("color"),
            "start_time": shift.get("start_time"),
            "end_time": shift.get("end_time"),
            "overnight": shift.get("overnight"),
            "on_call": sched.is_oncall(shift),
        }
    return _prune(attrs)


# ---------------------------------------------------------------------------
# Vacation
# ---------------------------------------------------------------------------
def _vacation_block(data: Data) -> Data:
    block = data.get(DATA_VACATION_BALANCE)
    return block if isinstance(block, dict) else {}


def _vacation_field(key: str) -> Callable[[Data], Any]:
    def value_fn(data: Data) -> Any:
        return _vacation_block(data).get(key)

    value_fn.__name__ = f"_vacation_{key}"
    return value_fn


def _vacation_total(data: Data) -> float | None:
    # total_available includes days saved from the previous year; fall back to
    # the base entitlement if the API ever omits it.
    block = _vacation_block(data)
    total = block.get("total_available")
    return block.get("entitled_days") if total is None else total


def _vacation_attrs(data: Data) -> Attrs:
    block = _vacation_block(data)
    if not block:
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
    attrs = {key: block[key] for key in keep if block.get(key) is not None}
    # Days currently booked as vacation in the fetched year, as a cross-check
    # against the payroll figure.
    if (days := _days_or_none(data, DATA_SCHEDULE_YEAR)) is not None:
        attrs["vacation_days_scheduled"] = _count_status(days, STATUS_VACATION)
    return attrs


# ---------------------------------------------------------------------------
# Pay
# ---------------------------------------------------------------------------
def _pay_block(data: Data) -> Data:
    block = data.get(DATA_PAY_MONTH)
    return block if isinstance(block, dict) else {}


def _money(key: str) -> Callable[[Data], float | None]:
    """Value function reading one rounded float out of /pay/month."""

    def value_fn(data: Data) -> float | None:
        return _as_float(_pay_block(data).get(key))

    value_fn.__name__ = f"_pay_{key}"
    return value_fn


def _count(key: str) -> Callable[[Data], int | None]:
    """Value function reading one integer out of /pay/month."""

    def value_fn(data: Data) -> int | None:
        return _as_int(_pay_block(data).get(key))

    value_fn.__name__ = f"_pay_{key}"
    return value_fn


def _ob_code_value(data: Data, field: str, code: str) -> float | None:
    """One OB code out of a per-code dict such as ob_pay or sick_ob_hours_by_code."""
    bucket = _pay_block(data).get(field)
    return _as_float(bucket.get(code)) if isinstance(bucket, dict) else None


def _sum_codes(field: str) -> Callable[[Data], float | None]:
    """Value function totalling a per-OB-code dict from /pay/month."""

    def value_fn(data: Data) -> float | None:
        bucket = _pay_block(data).get(field)
        if not isinstance(bucket, dict):
            return None
        amounts = (_as_float(value) for value in bucket.values())
        return round(sum(amount for amount in amounts if amount is not None), 2)

    value_fn.__name__ = f"_sum_{field}"
    return value_fn


def _per_code_attrs(data: Data, pay_field: str, hours_field: str) -> Attrs:
    """Break a pair of per-OB-code dicts out into flat attributes."""
    attrs: Attrs = {"total_hours": _sum_codes(hours_field)(data)}
    for code in OB_CODES:
        attrs[f"{code.lower()}_pay"] = _ob_code_value(data, pay_field, code)
        attrs[f"{code.lower()}_hours"] = _ob_code_value(data, hours_field, code)
    return _prune(attrs)


def _pay_attrs(data: Data) -> Attrs:
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
    attrs = {key: block[key] for key in keep if block.get(key) is not None}
    attrs["ob_total_pay"] = _sum_codes("ob_pay")(data)
    return _prune(attrs)


def _ob_summary_attrs(data: Data) -> Attrs:
    if not _pay_block(data):
        return {}
    return _per_code_attrs(data, "ob_pay", "ob_hours")


def _sick_ob_summary_attrs(data: Data) -> Attrs:
    if not _pay_block(data):
        return {}
    attrs = _per_code_attrs(data, "sick_ob_pay_by_code", "sick_ob_hours_by_code")
    attrs["lost"] = _money("sick_ob_lost")(data)
    return _prune(attrs)


def _absence_summary_attrs(data: Data) -> Attrs:
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
    return {key: block[key] for key in keys if block.get(key) is not None}


# ---------------------------------------------------------------------------
# Absences and account
# ---------------------------------------------------------------------------
def _absences_count(data: Data) -> int | None:
    return None if data.get(DATA_ABSENCES) is None else len(sched.absence_items(data))


def _absences_attrs(data: Data) -> Attrs:
    return {"absences": sched.absence_items(data)}


def _me_name(data: Data) -> str | None:
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return None
    if name := (me.get("name") or me.get("username")):
        return str(name)
    uid = me.get("id")
    return f"User {uid}" if uid is not None else None


def _me_attrs(data: Data) -> Attrs:
    """Scalar fields from /me (id, username, role, is_active, ...)."""
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return {}
    return {key: value for key, value in me.items() if isinstance(value, (str, int, float, bool))}


# ---------------------------------------------------------------------------
# Descriptions
#
# Names come from translations/en.json via translation_key; no description
# carries a redundant `name=` that Home Assistant would ignore anyway.
# ---------------------------------------------------------------------------
SENSOR_DESCRIPTIONS: tuple[PeriodicalSensorDescription, ...] = (
    # --- Today -------------------------------------------------------------
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
        key="status_today",
        translation_key="status_today",
        icon="mdi:information-outline",
        value_fn=_status_today,
        attr_fn=_status_attrs,
    ),
    PeriodicalSensorDescription(
        key="coworkers_today",
        translation_key="coworkers_today",
        icon="mdi:account-group",
        native_unit_of_measurement="people",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_coworkers_count,
        attr_fn=_coworkers_attrs,
    ),
    PeriodicalSensorDescription(
        key="ob_today",
        translation_key="ob_today",
        icon="mdi:cash-plus",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_ob_today,
    ),
    PeriodicalSensorDescription(
        key="rotation_week",
        translation_key="rotation_week",
        icon="mdi:rotate-right",
        value_fn=_rotation_week,
    ),
    # --- Upcoming ----------------------------------------------------------
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
        value_fn=_tomorrow_time("start_time"),
    ),
    PeriodicalSensorDescription(
        key="tomorrow_shift_end",
        translation_key="tomorrow_shift_end",
        icon="mdi:clock-end",
        value_fn=_tomorrow_time("end_time"),
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
        value_fn=_next_shift_time("start_time"),
    ),
    PeriodicalSensorDescription(
        key="next_shift_end",
        translation_key="next_shift_end",
        icon="mdi:clock-end",
        value_fn=_next_shift_time("end_time"),
    ),
    # --- Aggregates --------------------------------------------------------
    PeriodicalSensorDescription(
        key="shifts_this_week",
        translation_key="shifts_this_week",
        icon="mdi:calendar-week",
        native_unit_of_measurement=UNIT_SHIFTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_week_shifts,
        attr_fn=_week_attrs,
    ),
    PeriodicalSensorDescription(
        key="hours_this_week",
        translation_key="hours_this_week",
        icon="mdi:clock-outline",
        native_unit_of_measurement=UNIT_HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_week_hours,
        attr_fn=_week_hours_attrs,
    ),
    PeriodicalSensorDescription(
        key="working_days_month",
        translation_key="working_days_month",
        icon="mdi:calendar-month",
        native_unit_of_measurement=UNIT_DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_month_working_days,
        attr_fn=_month_attrs,
    ),
    PeriodicalSensorDescription(
        key="shifts_this_year",
        translation_key="shifts_this_year",
        icon="mdi:calendar-blank-multiple",
        native_unit_of_measurement=UNIT_SHIFTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_shifts,
    ),
    PeriodicalSensorDescription(
        key="shifts_remaining_year",
        translation_key="shifts_remaining_year",
        icon="mdi:calendar-arrow-right",
        native_unit_of_measurement=UNIT_SHIFTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_remaining_shifts,
    ),
    PeriodicalSensorDescription(
        key="hours_this_year",
        translation_key="hours_this_year",
        icon="mdi:clock-check-outline",
        native_unit_of_measurement=UNIT_HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_hours,
        attr_fn=_year_hours_attrs,
    ),
    # --- Vacation ----------------------------------------------------------
    PeriodicalSensorDescription(
        key="vacation_remaining",
        translation_key="vacation_remaining",
        icon="mdi:beach",
        native_unit_of_measurement=UNIT_DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_vacation_field("remaining_days"),
        attr_fn=_vacation_attrs,
    ),
    PeriodicalSensorDescription(
        key="vacation_used",
        translation_key="vacation_used",
        icon="mdi:umbrella-beach",
        native_unit_of_measurement=UNIT_DAYS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_vacation_field("used_days"),
    ),
    PeriodicalSensorDescription(
        key="vacation_total",
        translation_key="vacation_total",
        icon="mdi:calendar-check",
        native_unit_of_measurement=UNIT_DAYS,
        value_fn=_vacation_total,
    ),
    # --- Pay ---------------------------------------------------------------
    PeriodicalSensorDescription(
        key="pay_month_netto",
        translation_key="pay_month_netto",
        icon="mdi:cash-multiple",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("netto_pay"),
        attr_fn=_pay_attrs,
    ),
    PeriodicalSensorDescription(
        key="pay_month_gross",
        translation_key="pay_month_gross",
        icon="mdi:currency-usd",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("brutto_pay"),
    ),
    PeriodicalSensorDescription(
        key="pay_month_hours",
        translation_key="pay_month_hours",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UNIT_HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("total_hours"),
    ),
    PeriodicalSensorDescription(
        key="pay_month_shifts",
        translation_key="pay_month_shifts",
        icon="mdi:calendar-clock",
        native_unit_of_measurement=UNIT_SHIFTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("num_shifts"),
    ),
    PeriodicalSensorDescription(
        key="pay_oncall_month",
        translation_key="pay_oncall_month",
        icon="mdi:phone-clock",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("oncall_pay"),
    ),
    PeriodicalSensorDescription(
        key="pay_oncall_hours_month",
        translation_key="pay_oncall_hours_month",
        icon="mdi:phone-clock",
        native_unit_of_measurement=UNIT_HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("oncall_hours"),
    ),
    PeriodicalSensorDescription(
        key="pay_overtime_month",
        translation_key="pay_overtime_month",
        icon="mdi:timer-plus-outline",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("ot_pay"),
    ),
    PeriodicalSensorDescription(
        key="pay_sick_days_month",
        translation_key="pay_sick_days_month",
        icon="mdi:emoticon-sick-outline",
        native_unit_of_measurement=UNIT_DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("sick_days"),
    ),
    PeriodicalSensorDescription(
        key="pay_sick_hours_month",
        translation_key="pay_sick_hours_month",
        icon="mdi:emoticon-sick-outline",
        native_unit_of_measurement=UNIT_HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("sick_hours"),
    ),
    PeriodicalSensorDescription(
        key="pay_vab_days_month",
        translation_key="pay_vab_days_month",
        icon="mdi:baby-face-outline",
        native_unit_of_measurement=UNIT_DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("vab_days"),
    ),
    PeriodicalSensorDescription(
        key="pay_leave_days_month",
        translation_key="pay_leave_days_month",
        icon="mdi:calendar-minus",
        native_unit_of_measurement=UNIT_DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count("leave_days"),
    ),
    # Family summaries: these have no per-field equivalent, so they are the
    # only place the per-OB-code and per-absence-type figures are exposed.
    PeriodicalSensorDescription(
        key="ob_summary",
        translation_key="ob_summary",
        icon="mdi:cash-plus",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_sum_codes("ob_pay"),
        attr_fn=_ob_summary_attrs,
    ),
    PeriodicalSensorDescription(
        key="sick_ob_summary",
        translation_key="sick_ob_summary",
        icon="mdi:cash",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("sick_total_ob"),
        attr_fn=_sick_ob_summary_attrs,
    ),
    PeriodicalSensorDescription(
        key="absence_summary",
        translation_key="absence_summary",
        icon="mdi:cash-minus",
        native_unit_of_measurement=UNIT_SEK,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_money("absence_deduction"),
        attr_fn=_absence_summary_attrs,
    ),
    # --- Diagnostic --------------------------------------------------------
    PeriodicalSensorDescription(
        key="absences_count",
        translation_key="absences_count",
        icon="mdi:calendar-remove",
        native_unit_of_measurement="absences",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_absences_count,
        attr_fn=_absences_attrs,
    ),
    PeriodicalSensorDescription(
        key="account",
        translation_key="account",
        icon="mdi:account",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_me_name,
        attr_fn=_me_attrs,
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
        """Initialise the sensor from its description."""
        super().__init__(coordinator, entry, description, ENTITY_ID_FORMAT)

    @property
    def native_value(self) -> Any:
        """State, or None if the payload could not be read."""
        return self._evaluate(self.entity_description.value_fn, "value")
