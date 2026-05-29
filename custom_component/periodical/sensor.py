"""Sensor platform for Periodical."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable
from homeassistant.util import dt as dt_util

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_USER_ID,
    CONF_USER_NAME,
    DATA_ABSENCES,
    DATA_ME,  # /me payload — surfaced by the Account sensor (see _me_name/_me_attrs)
    DATA_NEXT_SHIFT,
    DATA_NEXT_SHIFT_TOMORROW,
    DATA_PAY_MONTH,
    DATA_SCHEDULE_MONTH,
    DATA_SCHEDULE_TODAY,
    DATA_SHIFTS,
    DATA_SCHEDULE_WEEK,
    DATA_SCHEDULE_YEAR,
    DATA_STATUS,
    DATA_VACATION_BALANCE,
    DOMAIN,
)
from .coordinator import PeriodicalCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PeriodicalSensorDescription(SensorEntityDescription):
    """Describe a Periodical sensor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None

def _hhmm_to_datetime(val: str | None, base_date: date | None = None) -> datetime | None:
    """Convert 'HH:MM' to a tz-aware datetime in HA's local zone (DST-correct)."""
    if not val:
        return None
    parts = val.split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        t = time(h, m)
        d = base_date or dt_util.now().date()
        return datetime.combine(d, t, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    except (IndexError, ValueError, TypeError):
        return None


def _parse_iso_date(val: str | None) -> date | None:
    """Parse 'YYYY-MM-DD' → date object (required by DATE device class)."""
    if not val:
        return None
    try:
        return date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _shift_attrs_from_ns(ns: dict) -> dict[str, Any]:
    """Standard attribute dict from a next-shift response."""
    attrs: dict[str, Any] = {
        "days_from_today": ns.get("days_from_today"),
        "rotation_week":   ns.get("rotation_week"),
    }
    shift = ns.get("shift")
    if isinstance(shift, dict):
        attrs["shift_code"]  = shift.get("code")
        attrs["shift_label"] = shift.get("label")
        attrs["shift_color"] = shift.get("color")
        attrs["start_time"]  = shift.get("start_time")
        attrs["end_time"]    = shift.get("end_time")
        attrs["overnight"]   = shift.get("overnight")
    return {k: v for k, v in attrs.items() if v is not None}


def _get_day_list(data_block: Any) -> list[dict]:
    """Extract a list of day objects from a schedule response.

    The API returns schedule/week and schedule/year but doesn't publish
    the response schema.  We probe the most common shapes:
      - a list directly
      - {"days": [...]}
      - {"schedule": [...]}
      - {"shifts": [...]}
      - {"weeks": [{"days": [...]}]}   (year response)
    """
    if isinstance(data_block, list):
        return data_block
    if not isinstance(data_block, dict):
        return []
    for key in ("days", "schedule", "shifts"):
        val = data_block.get(key)
        if isinstance(val, list):
            return val

    weeks = data_block.get("weeks")
    if isinstance(weeks, list):
        days: list[dict] = []
        for week in weeks:
            days.extend(_get_day_list(week))
        return days
    return []


def _shift_index(data: dict) -> dict[str, dict]:
    """Map shift code -> canonical definition from the /shifts catalog."""
    defs = data.get(DATA_SHIFTS)
    if isinstance(defs, list):
        return {d["code"]: d for d in defs if isinstance(d, dict) and d.get("code")}
    if isinstance(defs, dict):
        return defs
    return {}


def _day_is_working(day: dict) -> bool:
    """Return True if a day dict represents a working shift."""
    status = day.get("status")
    if isinstance(status, str):
        return status.lower() == "working"
    shift = day.get("shift")
    if isinstance(shift, dict) and shift.get("start_time"):
        return True
    return bool(day.get("start_time"))


def _day_hours(day: dict, index: dict[str, dict] | None = None) -> float:
    """Estimate hours from a day dict.

    Prefers an explicit total_hours, otherwise derives the span from
    start_time/end_time.  The authoritative `overnight` flag (from /shifts,
    looked up by code when missing on the day) disambiguates shifts whose
    clock times are equal, e.g. on-call 00:00->00:00 = 24h.
    """
    if day.get("total_hours"):
        try:
            return float(day["total_hours"])
        except (TypeError, ValueError):
            pass
    shift = day.get("shift") if isinstance(day.get("shift"), dict) else day
    start_str = shift.get("start_time")
    end_str   = shift.get("end_time")
    overnight = shift.get("overnight")
    code = shift.get("code")
    if index and code in index:
        canon = index[code]
        start_str = start_str or canon.get("start_time")
        end_str   = end_str or canon.get("end_time")
        if overnight is None:
            overnight = canon.get("overnight")
    if not start_str or not end_str:
        return 0.0
    try:
        sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
        eh, em = int(end_str.split(":")[0]),   int(end_str.split(":")[1])
        duration = (eh * 60 + em) - (sh * 60 + sm)
        if duration < 0:                       # clearly crosses midnight
            duration += 24 * 60
        elif duration == 0:                    # equal times: 24h iff overnight
            duration = 24 * 60 if overnight else 0
        return round(duration / 60, 2)
    except (IndexError, ValueError):
        return 0.0

def _active_shift_and_date(data: dict) -> tuple[dict, date | None] | tuple[None, None]:
    """Return today's relevant shift and the calendar date its start belongs to.

    When a shift from the previous day is still running past midnight, /status
    reports it under `currently_active_shift` with its original start `date`
    (e.g. at 02:00 on the 30th, the N3 that began 22:00 on the 29th).  Preferring
    that anchors the start/end timestamps to the correct days instead of assuming
    both fall on "today".  Otherwise we use the plain shift on status/today, dated
    by that response's own `date` field (falling back to HA-local today).
    """
    status = data.get(DATA_STATUS)
    if isinstance(status, dict):
        active = status.get("currently_active_shift")
        if isinstance(active, dict):
            shift = active.get("shift")
            if isinstance(shift, dict) and shift.get("start_time"):
                return shift, _parse_iso_date(active.get("date"))

    for key in (DATA_STATUS, DATA_SCHEDULE_TODAY):
        day = data.get(key)
        if isinstance(day, dict):
            shift = day.get("shift")
            if isinstance(shift, dict) and shift.get("start_time"):
                return shift, _parse_iso_date(day.get("date"))
    return None, None


def _get_today_shift(data: dict) -> dict | None:
    """The shift dict relevant right now (active overnight shift takes priority)."""
    shift, _ = _active_shift_and_date(data)
    return shift


def _today_start(data: dict) -> datetime | None:
    shift, base = _active_shift_and_date(data)
    if not shift:
        return None
    return _hhmm_to_datetime(shift.get("start_time"), base)


def _today_end(data: dict) -> datetime | None:
    shift, base = _active_shift_and_date(data)
    if not shift:
        return None
    start_dt = _hhmm_to_datetime(shift.get("start_time"), base)
    end_dt   = _hhmm_to_datetime(shift.get("end_time"), base)
    overnight = shift.get("overnight")
    if overnight is None:
        code = shift.get("code")
        canon = _shift_index(data).get(code) if code else None
        if canon:
            overnight = canon.get("overnight")
    # End rolls to the next day when the clock wraps past midnight, or when start
    # and end are equal but the shift is flagged overnight (e.g. on-call 00:00->00:00).
    if end_dt and start_dt and (end_dt < start_dt or (end_dt == start_dt and overnight)):
        end_dt += timedelta(days=1)
    return end_dt


def _today_shift_attrs(data: dict) -> dict[str, Any]:
    shift = _get_today_shift(data)
    if not shift:
        return {}
    return {
        "shift_code":  shift.get("code"),
        "shift_label": shift.get("label"),
        "shift_color": shift.get("color"),
        "start_time":  shift.get("start_time"),
        "end_time":    shift.get("end_time"),
        "overnight":   shift.get("overnight"),
    }

def _get_coworkers(data: dict) -> list[dict]:
    for key in (DATA_STATUS, DATA_SCHEDULE_TODAY):
        day = data.get(key)
        if isinstance(day, dict):
            cw = day.get("coworkers") or day.get("co_workers") or []
            if cw:
                return cw
    return []


def _today_coworkers_count(data: dict) -> int:
    return len(_get_coworkers(data))


def _today_coworkers_attrs(data: dict) -> dict[str, Any]:
    return {
        "co_workers": [
            {
                "name":        cw.get("name"),
                "shift_code":  cw.get("shift_code"),
                "shift_label": cw.get("shift_label"),
            }
            for cw in _get_coworkers(data)
        ]
    }


def _status_today(data: dict) -> str | None:
    st = data.get(DATA_STATUS) or {}
    return st.get("status")


def _ob_total(data: dict) -> float | None:
    st = data.get(DATA_STATUS) or {}
    val = st.get("ob_total") or st.get("ob") or st.get("ob_supplement")
    try:
        return round(float(val), 2) if val is not None else None
    except (TypeError, ValueError):
        return None


def _rotation_week(data: dict) -> int | None:
    st = data.get(DATA_STATUS) or {}
    val = st.get("rotation_week")
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _status_attrs(data: dict) -> dict[str, Any]:
    """Only fields without a dedicated sensor; shift detail lives on shift_start_today."""
    st = data.get(DATA_STATUS) or {}
    return {k: st[k] for k in ("overtime", "partial_day") if k in st}


def _week_shifts_count(data: dict) -> int | None:
    sw = data.get(DATA_SCHEDULE_WEEK)
    if sw is None:
        return None
    days = _get_day_list(sw)
    return sum(1 for d in days if _day_is_working(d))


def _week_hours(data: dict) -> float | None:
    sw = data.get(DATA_SCHEDULE_WEEK)
    if sw is None:
        return None
    index = _shift_index(data)
    days = _get_day_list(sw)
    total = sum(_day_hours(d, index) for d in days if _day_is_working(d))
    return round(total, 2) if total else None


def _week_attrs(data: dict) -> dict[str, Any]:
    sw = data.get(DATA_SCHEDULE_WEEK)
    if sw is None:
        return {}
    days = _get_day_list(sw)
    schedule = []
    for d in days:
        shift = d.get("shift") if isinstance(d.get("shift"), dict) else {}
        schedule.append({
            "date":        d.get("date"),
            "status":      d.get("status"),
            "shift_code":  shift.get("code")  or d.get("shift_code"),
            "shift_label": shift.get("label") or d.get("shift_label"),
            "start_time":  shift.get("start_time") or d.get("start_time"),
            "end_time":    shift.get("end_time")   or d.get("end_time"),
        })
    return {"days": [s for s in schedule if any(v for v in s.values())]}


def _year_total_shifts(data: dict) -> int | None:
    sy = data.get(DATA_SCHEDULE_YEAR)
    if sy is None:
        return None
    # Try explicit summary fields first
    for key in ("total_shifts", "num_shifts", "shift_count"):
        val = sy.get(key) if isinstance(sy, dict) else None
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    days = _get_day_list(sy)
    return sum(1 for d in days if _day_is_working(d)) or None


def _year_remaining_shifts(data: dict) -> int | None:
    sy = data.get(DATA_SCHEDULE_YEAR)
    if sy is None:
        return None
    today_str = dt_util.now().date().isoformat()
    days = _get_day_list(sy)
    return sum(
        1 for d in days
        if _day_is_working(d) and (d.get("date") or "") >= today_str
    ) or None


def _year_total_hours(data: dict) -> float | None:
    sy = data.get(DATA_SCHEDULE_YEAR)
    if sy is None:
        return None
    if isinstance(sy, dict):
        for key in ("total_hours", "hours"):
            val = sy.get(key)
            if val is not None:
                try:
                    return round(float(val), 2)
                except (TypeError, ValueError):
                    pass
    index = _shift_index(data)
    days = _get_day_list(sy)
    total = sum(_day_hours(d, index) for d in days if _day_is_working(d))
    return round(total, 2) if total else None


def _next_shift_date(data: dict) -> date | None:
    ns = data.get(DATA_NEXT_SHIFT)
    return _parse_iso_date(ns.get("date")) if ns else None


def _next_shift_start(data: dict) -> str | None:
    ns = data.get(DATA_NEXT_SHIFT)
    return (ns.get("shift") or {}).get("start_time") if ns else None


def _next_shift_end(data: dict) -> str | None:
    ns = data.get(DATA_NEXT_SHIFT)
    return (ns.get("shift") or {}).get("end_time") if ns else None


def _next_shift_attrs(data: dict) -> dict[str, Any]:
    return _shift_attrs_from_ns(data.get(DATA_NEXT_SHIFT) or {})


def _tomorrow_shift_date(data: dict) -> date | None:
    ns = data.get(DATA_NEXT_SHIFT_TOMORROW)
    return _parse_iso_date(ns.get("date")) if ns else None


def _tomorrow_shift_start(data: dict) -> str | None:
    ns = data.get(DATA_NEXT_SHIFT_TOMORROW)
    return (ns.get("shift") or {}).get("start_time") if ns else None


def _tomorrow_shift_end(data: dict) -> str | None:
    ns = data.get(DATA_NEXT_SHIFT_TOMORROW)
    return (ns.get("shift") or {}).get("end_time") if ns else None


def _tomorrow_shift_attrs(data: dict) -> dict[str, Any]:
    return _shift_attrs_from_ns(data.get(DATA_NEXT_SHIFT_TOMORROW) or {})


# Vacation field names confirmed via /vacation/balance:
#   remaining_days, used_days, entitled_days, total_available (= entitled + saved),
#   saved_from_previous, year_start, year_end, projection{...}.
def _vacation_remaining(data: dict) -> float | None:
    vb = data.get(DATA_VACATION_BALANCE)
    return vb.get("remaining_days") if isinstance(vb, dict) else None


def _vacation_used(data: dict) -> float | None:
    vb = data.get(DATA_VACATION_BALANCE)
    return vb.get("used_days") if isinstance(vb, dict) else None


def _vacation_total(data: dict) -> float | None:
    # total_available includes days saved from the previous year; fall back to the
    # base entitlement if the API ever omits it.
    vb = data.get(DATA_VACATION_BALANCE)
    if not isinstance(vb, dict):
        return None
    return vb.get("total_available") or vb.get("entitled_days")


def _vacation_attrs(data: dict) -> dict[str, Any]:
    """Non-duplicated extras: the vacation-year window and the payout projection.

    remaining/used/total each have their own sensor, so they are intentionally
    left out here to avoid repeating sensor values as attributes.
    """
    vb = data.get(DATA_VACATION_BALANCE)
    if not isinstance(vb, dict):
        return {}
    keep = (
        "year", "year_start", "year_end",
        "entitled_days", "saved_from_previous",
        "is_first_year", "projection",
    )
    return {k: vb[k] for k in keep if k in vb and vb[k] is not None}


def _pay_float(data: dict, *keys: str) -> float | None:
    pm = data.get(DATA_PAY_MONTH)
    if not pm:
        return None
    for k in keys:
        val = pm.get(k)
        if val is not None:
            try:
                return round(float(val), 2)
            except (TypeError, ValueError):
                pass
    return None


def _pay_int(data: dict, *keys: str) -> int | None:
    pm = data.get(DATA_PAY_MONTH)
    if not pm:
        return None
    for k in keys:
        val = pm.get(k)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return None


def _pay_brutto(data: dict) -> float | None:
    return _pay_float(data, "brutto_pay")


def _pay_netto(data: dict) -> float | None:
    return _pay_float(data, "netto_pay")


def _pay_hours(data: dict) -> float | None:
    return _pay_float(data, "total_hours")


def _pay_shifts(data: dict) -> int | None:
    return _pay_int(data, "num_shifts")


def _pay_oncall(data: dict) -> float | None:
    return _pay_float(data, "oncall_pay")


def _pay_oncall_hours(data: dict) -> float | None:
    return _pay_float(data, "oncall_hours")


def _pay_overtime(data: dict) -> float | None:
    return _pay_float(data, "ot_pay")


def _pay_sick_days(data: dict) -> int | None:
    return _pay_int(data, "sick_days")


def _pay_sick_hours(data: dict) -> float | None:
    return _pay_float(data, "sick_hours")


def _pay_vab_days(data: dict) -> int | None:
    return _pay_int(data, "vab_days")


def _pay_leave_days(data: dict) -> int | None:
    return _pay_int(data, "leave_days")


def _pay_attrs(data: dict) -> dict[str, Any]:
    pm = data.get(DATA_PAY_MONTH) or {}
    # Real /pay/month keys that have no dedicated sensor. (gross/netto/hours/
    # shifts/oncall/ot/sick_days/sick_hours/vab_days/leave_days each have one.)
    # ob_pay and ob_hours are per-OB-code dicts (e.g. {"OB1": .., "OB2": ..}).
    keep = (
        "year", "month",
        "ob_pay", "ob_hours",
        "absence_deduction", "absence_hours",
        "vab_hours", "leave_hours",
        "parental_days", "parental_hours",
    )
    return {k: pm[k] for k in keep if k in pm}


def _absences_count(data: dict) -> int:
    ab = data.get(DATA_ABSENCES)
    if isinstance(ab, list):
        return len(ab)
    if isinstance(ab, dict):
        items = ab.get("absences") or ab.get("items") or []
        return len(items)
    return 0


def _absences_attrs(data: dict) -> dict[str, Any]:
    ab = data.get(DATA_ABSENCES)
    if isinstance(ab, list):
        return {"absences": ab}
    return ab or {}


def _schedule_month_working_days(data: dict) -> int | None:
    sm = data.get(DATA_SCHEDULE_MONTH)
    if not sm:
        return None
    for key in ("working_days", "num_shifts", "shift_count"):
        val = sm.get(key) if isinstance(sm, dict) else None
        if val is not None:
            return int(val)
    days = _get_day_list(sm)
    count = sum(1 for d in days if _day_is_working(d))
    return count or None


def _schedule_month_attrs(data: dict) -> dict[str, Any]:
    sm = data.get(DATA_SCHEDULE_MONTH) or {}
    if not isinstance(sm, dict):
        return {}
    return {
        k: v for k, v in sm.items()
        if k in ("month", "year", "total_hours", "working_days", "days_off", "num_shifts")
    }

# ---------------------------------------------------------------------------
# Account (/me)
#
# /me is the endpoint that identifies the authenticated user.  At config-flow
# time it is called once to discover the numeric user id, which is then stored
# on the config entry and used to build every other /users/{id}/... request.
# The coordinator also refreshes /me on the daily tier; the Account sensor below
# consumes that payload so the fetch is surfaced (name as state, profile fields
# as attributes) instead of being discarded.
# ---------------------------------------------------------------------------
def _me_name(data: dict) -> str | None:
    """Display name for the authenticated account, probed from the /me payload."""
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return None
    # Same field precedence the config flow uses to resolve a human-readable name.
    name = (
        me.get("name")
        or me.get("full_name")
        or me.get("username")
        or me.get("email")
    )
    if name:
        return str(name)
    uid = me.get("id") or me.get("user_id") or me.get("userId")
    return f"User {uid}" if uid is not None else None


def _me_attrs(data: dict) -> dict[str, Any]:
    """Pass through scalar fields from /me (id, email, role, …) as attributes.

    The /me schema is not published, so rather than hard-code field names we
    expose every top-level scalar value as-is; nested objects/lists are skipped
    to keep the attribute set flat and state-machine friendly.
    """
    me = data.get(DATA_ME)
    if not isinstance(me, dict):
        return {}
    return {
        k: v
        for k, v in me.items()
        if isinstance(v, (str, int, float, bool)) and v is not None
    }


SENSOR_DESCRIPTIONS: tuple[PeriodicalSensorDescription, ...] = (

    # Account identity from /me.  Diagnostic: it rarely changes and is mainly a
    # human-readable label / holder for profile attributes, not a primary metric.
    PeriodicalSensorDescription(
        key="account",
        translation_key="account",
        name="Account",
        icon="mdi:account",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_me_name,
        attr_fn=_me_attrs,
    ),
    PeriodicalSensorDescription(
        key="shift_start_today",
        translation_key="shift_start_today",
        name="Shift Start Today",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_today_start,
        attr_fn=_today_shift_attrs,
    ),
    PeriodicalSensorDescription(
        key="shift_end_today",
        translation_key="shift_end_today",
        name="Shift End Today",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_today_end,
        attr_fn=None,
    ),
    PeriodicalSensorDescription(
        key="coworkers_today",
        translation_key="coworkers_today",
        name="Co-workers Today",
        icon="mdi:account-group",
        native_unit_of_measurement="people",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_today_coworkers_count,
        attr_fn=_today_coworkers_attrs,
    ),

    PeriodicalSensorDescription(
        key="status_today",
        translation_key="status_today",
        name="Status Today",
        icon="mdi:information-outline",
        value_fn=_status_today,
        attr_fn=_status_attrs,
    ),
    PeriodicalSensorDescription(
        key="ob_today",
        translation_key="ob_today",
        name="OB Supplement Today",
        icon="mdi:cash-plus",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_ob_total,
        attr_fn=None,
    ),
    PeriodicalSensorDescription(
        key="rotation_week",
        translation_key="rotation_week",
        name="Rotation Week",
        icon="mdi:rotate-right",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_rotation_week,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="shifts_this_week",
        translation_key="shifts_this_week",
        name="Shifts This Week",
        icon="mdi:calendar-week",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_week_shifts_count,
        attr_fn=_week_attrs,
    ),
    PeriodicalSensorDescription(
        key="hours_this_week",
        translation_key="hours_this_week",
        name="Hours This Week",
        icon="mdi:clock-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_week_hours,
        attr_fn=_week_attrs,
    ),

    PeriodicalSensorDescription(
        key="working_days_month",
        translation_key="working_days_month",
        name="Working Days This Month",
        icon="mdi:calendar-month",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_schedule_month_working_days,
        attr_fn=_schedule_month_attrs,
    ),

    PeriodicalSensorDescription(
        key="shifts_this_year",
        translation_key="shifts_this_year",
        name="Shifts This Year",
        icon="mdi:calendar-blank-multiple",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_total_shifts,
        attr_fn=None,
    ),
    PeriodicalSensorDescription(
        key="shifts_remaining_year",
        translation_key="shifts_remaining_year",
        name="Shifts Remaining This Year",
        icon="mdi:calendar-arrow-right",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_remaining_shifts,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="hours_this_year",
        translation_key="hours_this_year",
        name="Hours This Year",
        icon="mdi:clock-check-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_year_total_hours,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="next_shift_date",
        translation_key="next_shift_date",
        name="Next Shift Date",
        icon="mdi:calendar-arrow-right",
        device_class=SensorDeviceClass.DATE,
        value_fn=_next_shift_date,
        attr_fn=_next_shift_attrs,
    ),
    
    PeriodicalSensorDescription(
        key="next_shift_start",
        translation_key="next_shift_start",
        name="Next Shift Start",
        icon="mdi:clock-start",
        value_fn=_next_shift_start,
        attr_fn=None,
    ),
    
    PeriodicalSensorDescription(
        key="next_shift_end",
        translation_key="next_shift_end",
        name="Next Shift End",
        icon="mdi:clock-end",
        value_fn=_next_shift_end,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="tomorrow_shift_date",
        translation_key="tomorrow_shift_date",
        name="Tomorrow Shift Date",
        icon="mdi:account-clock",
        device_class=SensorDeviceClass.DATE,
        value_fn=_tomorrow_shift_date,
        attr_fn=_tomorrow_shift_attrs,
    ),
    
    PeriodicalSensorDescription(
        key="tomorrow_shift_start",
        translation_key="tomorrow_shift_start",
        name="Tomorrow Shift Start",
        icon="mdi:clock-start",
        value_fn=_tomorrow_shift_start,
        attr_fn=None,
    ),
    PeriodicalSensorDescription(
        key="tomorrow_shift_end",
        translation_key="tomorrow_shift_end",
        name="Tomorrow Shift End",
        icon="mdi:clock-end",
        value_fn=_tomorrow_shift_end,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="vacation_remaining",
        translation_key="vacation_remaining",
        name="Vacation Days Remaining",
        icon="mdi:beach",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_vacation_remaining,
        attr_fn=_vacation_attrs,
    ),
    
    PeriodicalSensorDescription(
        key="vacation_used",
        translation_key="vacation_used",
        name="Vacation Days Used",
        icon="mdi:umbrella-beach",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_vacation_used,
        attr_fn=None,
    ),
    
    PeriodicalSensorDescription(
        key="vacation_total",
        translation_key="vacation_total",
        name="Vacation Days Total",
        icon="mdi:calendar-check",
        native_unit_of_measurement="days",
        value_fn=_vacation_total,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="pay_month_gross",
        translation_key="pay_month_gross",
        name="Monthly Pay (Gross)",
        icon="mdi:currency-usd",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_brutto,
        attr_fn=_pay_attrs,
    ),
    
    PeriodicalSensorDescription(
        key="pay_month_netto",
        translation_key="pay_month_netto",
        name="Monthly Pay (Netto)",
        icon="mdi:currency-usd",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_netto,
        attr_fn=None,
    ),
    
    PeriodicalSensorDescription(
        key="pay_month_hours",
        translation_key="pay_month_hours",
        name="Monthly Hours Worked",
        icon="mdi:timer-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_hours,
        attr_fn=None,
    ),
    
    PeriodicalSensorDescription(
        key="pay_month_shifts",
        translation_key="pay_month_shifts",
        name="Monthly Shifts",
        icon="mdi:calendar-clock",
        native_unit_of_measurement="shifts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_shifts,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="pay_oncall_month",
        translation_key="pay_oncall_month",
        name="Monthly On-call Pay",
        icon="mdi:phone-clock",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_oncall,
        attr_fn=None,
    ),
    
    PeriodicalSensorDescription(
        key="pay_oncall_hours_month",
        translation_key="pay_oncall_hours_month",
        name="Monthly On-call Hours",
        icon="mdi:phone-clock",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_oncall_hours,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="pay_overtime_month",
        translation_key="pay_overtime_month",
        name="Monthly Overtime Pay",
        icon="mdi:timer-plus-outline",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_overtime,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="pay_sick_days_month",
        translation_key="pay_sick_days_month",
        name="Monthly Sick Days",
        icon="mdi:emoticon-sick-outline",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_sick_days,
        attr_fn=None,
    ),
    PeriodicalSensorDescription(
        key="pay_sick_hours_month",
        translation_key="pay_sick_hours_month",
        name="Monthly Sick Hours",
        icon="mdi:emoticon-sick-outline",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_sick_hours,
        attr_fn=None,
    ),
    PeriodicalSensorDescription(
        key="pay_vab_days_month",
        translation_key="pay_vab_days_month",
        name="Monthly VAB Days",
        icon="mdi:baby-face-outline",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_vab_days,
        attr_fn=None,
    ),
    PeriodicalSensorDescription(
        key="pay_leave_days_month",
        translation_key="pay_leave_days_month",
        name="Monthly Leave Days",
        icon="mdi:calendar-minus",
        native_unit_of_measurement="days",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pay_leave_days,
        attr_fn=None,
    ),

    PeriodicalSensorDescription(
        key="absences_count",
        translation_key="absences_count",
        name="Absences This Year",
        icon="mdi:calendar-remove",
        native_unit_of_measurement="absences",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_absences_count,
        attr_fn=_absences_attrs,
    ),
)


# ---------------------------------------------------------------------------
# 1:1 field coverage
#
# The curated sensors above cover the primary metrics.  The block below adds a
# dedicated sensor for every *remaining* field the API actually returns, so the
# integration exposes the payloads one-to-one.  These are generated rather than
# hand-written to keep them in lockstep with the real schema (confirmed from
# live /pay/month, /vacation/balance and /me responses).
#
# Generated sensors use an explicit `name` (no translation_key) so they need no
# strings.json entry; with has_entity_name the UI shows "<user> <name>".
#
# Per-code OB dicts (ob_pay / ob_hours / sick_ob_pay_by_code / sick_ob_hours_by_code,
# each shaped like {"OB1": .., ... "OB5": ..}) are broken out into one sensor per
# OB code, over the fixed OB1-OB5 set the payroll uses.  If a new OB code is ever
# introduced, add it to OB_CODES below and a matching set of sensors is generated.
# ---------------------------------------------------------------------------
OB_CODES = ("OB1", "OB2", "OB3", "OB4", "OB5")


def _pay_ob(data: dict, field: str, code: str) -> float | None:
    """One OB-code value out of the pay/month ob_pay or ob_hours dict."""
    pm = data.get(DATA_PAY_MONTH)
    if not isinstance(pm, dict):
        return None
    bucket = pm.get(field)
    if not isinstance(bucket, dict):
        return None
    val = bucket.get(code)
    try:
        return round(float(val), 2) if val is not None else None
    except (TypeError, ValueError):
        return None


def _vac_field(data: dict, key: str) -> Any:
    vb = data.get(DATA_VACATION_BALANCE)
    return vb.get(key) if isinstance(vb, dict) else None


def _vac_proj(data: dict, key: str) -> Any:
    vb = data.get(DATA_VACATION_BALANCE)
    if not isinstance(vb, dict):
        return None
    proj = vb.get("projection")
    return proj.get(key) if isinstance(proj, dict) else None


def _me_field(data: dict, key: str) -> Any:
    me = data.get(DATA_ME)
    return me.get(key) if isinstance(me, dict) else None


def _build_field_descriptions() -> list[PeriodicalSensorDescription]:
    """Granular field sensors are intentionally NOT generated.

    Every field these used to expose (per-OB-code pay/hours, sick-OB by code,
    absence/parental/leave/vab hours, the full vacation projection, /me profile
    fields) is now carried as attributes on the compound summary sensors below
    (Monthly OB, Monthly Sick OB, Monthly Absence, Vacation) and on the Account
    sensor — collapsing ~40 individual entities into a handful of rich ones.

    Kept as an empty hook so the call site and any future opt-in stays simple.
    """
    return []


SENSOR_DESCRIPTIONS = SENSOR_DESCRIPTIONS + tuple(_build_field_descriptions())


# ---------------------------------------------------------------------------
# Compound (summary) sensors
#
# These collapse a whole family of fields into a single enabled-by-default
# sensor: the state is the headline number, the rest ride along as attributes.
# They summarize each family into one headline sensor for convenient dashboards;
# the granular per-field sensors above remain available alongside them.
#
# Note: attributes are not recorded in history — use the granular per-field
# sensors for long-term graphing/alerting on a specific value.
# ---------------------------------------------------------------------------
def _sum_dict(data: dict, field: str) -> float | None:
    pm = data.get(DATA_PAY_MONTH)
    if not isinstance(pm, dict) or not isinstance(pm.get(field), dict):
        return None
    total = 0.0
    for v in pm[field].values():
        try:
            total += float(v)
        except (TypeError, ValueError):
            pass
    return round(total, 2)


def _ob_compound_attrs(data: dict) -> dict[str, Any]:
    pm = data.get(DATA_PAY_MONTH)
    if not isinstance(pm, dict):
        return {}
    attrs: dict[str, Any] = {"total_hours": _sum_dict(data, "ob_hours")}
    for code in OB_CODES:
        attrs[f"{code.lower()}_pay"] = _pay_ob(data, "ob_pay", code)
        attrs[f"{code.lower()}_hours"] = _pay_ob(data, "ob_hours", code)
    return {k: v for k, v in attrs.items() if v is not None}


def _sick_ob_compound_attrs(data: dict) -> dict[str, Any]:
    pm = data.get(DATA_PAY_MONTH)
    if not isinstance(pm, dict):
        return {}
    attrs: dict[str, Any] = {
        "lost": _pay_float(data, "sick_ob_lost"),
        "total_hours": _sum_dict(data, "sick_ob_hours_by_code"),
    }
    for code in OB_CODES:
        attrs[f"{code.lower()}_pay"] = _pay_ob(data, "sick_ob_pay_by_code", code)
        attrs[f"{code.lower()}_hours"] = _pay_ob(data, "sick_ob_hours_by_code", code)
    return {k: v for k, v in attrs.items() if v is not None}


def _absence_compound_attrs(data: dict) -> dict[str, Any]:
    pm = data.get(DATA_PAY_MONTH)
    if not isinstance(pm, dict):
        return {}
    keys = (
        "absence_hours", "sick_days", "sick_hours", "vab_days", "vab_hours",
        "leave_days", "leave_hours", "parental_days", "parental_hours",
    )
    return {k: pm[k] for k in keys if k in pm and pm[k] is not None}


def _pay_compound_attrs(data: dict) -> dict[str, Any]:
    pm = data.get(DATA_PAY_MONTH)
    if not isinstance(pm, dict):
        return {}
    out: dict[str, Any] = {}
    for k in ("brutto_pay", "total_hours", "num_shifts", "oncall_pay",
              "oncall_hours", "ot_pay", "year", "month"):
        if pm.get(k) is not None:
            out[k] = pm[k]
    out["ob_total_pay"] = _sum_dict(data, "ob_pay")
    return {k: v for k, v in out.items() if v is not None}


def _vacation_compound_attrs(data: dict) -> dict[str, Any]:
    vb = data.get(DATA_VACATION_BALANCE)
    if not isinstance(vb, dict):
        return {}
    keys = (
        "used_days", "total_available", "entitled_days", "saved_from_previous",
        "year", "year_start", "year_end", "is_first_year",
    )
    out = {k: vb[k] for k in keys if k in vb and vb[k] is not None}
    proj = vb.get("projection")
    if isinstance(proj, dict):
        out["projection"] = proj
    return out


COMPOUND_DESCRIPTIONS: tuple[PeriodicalSensorDescription, ...] = (
    # Monthly net pay is the headline; gross/hours/shifts/oncall/ot ride along.
    PeriodicalSensorDescription(
        key="pay_summary",
        name="Monthly Pay",
        icon="mdi:cash-multiple",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _pay_float(d, "netto_pay"),
        attr_fn=_pay_compound_attrs,
    ),
    # Total OB pay; per-code pay/hours in attributes.
    PeriodicalSensorDescription(
        key="ob_summary",
        name="Monthly OB",
        icon="mdi:cash-plus",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _sum_dict(d, "ob_pay"),
        attr_fn=_ob_compound_attrs,
    ),
    # Sick-OB total; per-code sick pay/hours + lost in attributes.
    PeriodicalSensorDescription(
        key="sick_ob_summary",
        name="Monthly Sick OB",
        icon="mdi:cash",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _pay_float(d, "sick_total_ob"),
        attr_fn=_sick_ob_compound_attrs,
    ),
    # Absence deduction is the headline; day/hour counts by type in attributes.
    PeriodicalSensorDescription(
        key="absence_summary",
        name="Monthly Absence",
        icon="mdi:cash-minus",
        native_unit_of_measurement="SEK",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _pay_float(d, "absence_deduction"),
        attr_fn=_absence_compound_attrs,
    ),
    # Vacation days remaining; used/total/entitled/year-window in attributes.
    PeriodicalSensorDescription(
        key="vacation_summary",
        name="Vacation",
        icon="mdi:beach",
        native_unit_of_measurement="days",
        value_fn=_vacation_remaining,
        attr_fn=_vacation_compound_attrs,
    ),
)

SENSOR_DESCRIPTIONS = SENSOR_DESCRIPTIONS + COMPOUND_DESCRIPTIONS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Periodical sensors."""
    coordinator: PeriodicalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PeriodicalSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class PeriodicalSensor(CoordinatorEntity[PeriodicalCoordinator], SensorEntity):
    """A single Periodical sensor."""

    entity_description: PeriodicalSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PeriodicalCoordinator,
        entry: ConfigEntry,
        description: PeriodicalSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        user_name = entry.data.get(CONF_USER_NAME, "Periodical")
        user_id   = entry.data[CONF_USER_ID]
        self._attr_unique_id = f"{DOMAIN}_{user_id}_{description.key}"
        # Force a stable, integration-prefixed entity_id (sensor.periodical_<key>)
        # regardless of the device/user name, so all entities sort together.
        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT, f"{DOMAIN}_{description.key}", hass=coordinator.hass
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(user_id))},
            name=user_name,
            manufacturer="Periodical",
            model="Periodical API",
            entry_type=None,
        )

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Error extracting value for %s",
                self.entity_description.key,
                exc_info=True,
            )
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None or self.entity_description.attr_fn is None:
            return {}
        try:
            return self.entity_description.attr_fn(self.coordinator.data) or {}
        except Exception: 
            return {}
