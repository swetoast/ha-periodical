"""Sensor platform for Periodical."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, timedelta
from typing import Any, Callable
import time as _time_mod

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_USER_ID,
    CONF_USER_NAME,
    DATA_ABSENCES,
    DATA_ME,
    DATA_NEXT_SHIFT,
    DATA_NEXT_SHIFT_TOMORROW,
    DATA_PAY_MONTH,
    DATA_SCHEDULE_MONTH,
    DATA_SCHEDULE_TODAY,
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

def _local_tz() -> timezone:
    """Return the local UTC offset as a fixed-offset timezone."""
    offset_sec = -(_time_mod.altzone if _time_mod.daylight else _time_mod.timezone)
    return timezone(timedelta(seconds=offset_sec))


def _hhmm_to_datetime(val: str | None, base_date: date | None = None) -> datetime | None:
    """Convert 'HH:MM' to a tz-aware datetime.  base_date defaults to today."""
    if not val:
        return None
    parts = val.split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        t = time(h, m)
        d = base_date or date.today()
        return datetime.combine(d, t, tzinfo=_local_tz())
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


def _day_is_working(day: dict) -> bool:
    """Return True if a day dict represents a working shift."""
    status = day.get("status")
    if isinstance(status, str):
        return status.lower() == "working"
    shift = day.get("shift")
    if isinstance(shift, dict) and shift.get("start_time"):
        return True
    return bool(day.get("start_time"))


def _day_hours(day: dict) -> float:
    """Estimate hours from a day dict (start_time / end_time or total_hours)."""
    if day.get("total_hours"):
        try:
            return float(day["total_hours"])
        except (TypeError, ValueError):
            pass
    shift = day.get("shift") if isinstance(day.get("shift"), dict) else day
    start_str = shift.get("start_time")
    end_str   = shift.get("end_time")
    if not start_str or not end_str:
        return 0.0
    try:
        sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
        eh, em = int(end_str.split(":")[0]),   int(end_str.split(":")[1])
        total = (eh * 60 + em) - (sh * 60 + sm)
        if total < 0:   # overnight
            total += 24 * 60
        return round(total / 60, 2)
    except (IndexError, ValueError):
        return 0.0

def _get_today_shift(data: dict) -> dict | None:
    for key in (DATA_STATUS, DATA_SCHEDULE_TODAY):
        day = data.get(key)
        if isinstance(day, dict):
            shift = day.get("shift")
            if isinstance(shift, dict) and shift.get("start_time"):
                return shift
    return None


def _today_start(data: dict) -> datetime | None:
    shift = _get_today_shift(data)
    return _hhmm_to_datetime(shift.get("start_time") if shift else None)


def _today_end(data: dict) -> datetime | None:
    shift = _get_today_shift(data)
    if not shift:
        return None
    end_dt   = _hhmm_to_datetime(shift.get("end_time"))
    start_dt = _hhmm_to_datetime(shift.get("start_time"))
    if end_dt and start_dt and end_dt < start_dt:
        end_dt += timedelta(days=1)   # overnight shift
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
    st = data.get(DATA_STATUS) or {}
    attrs: dict[str, Any] = {}
    for k in ("status", "rotation_week", "overtime", "partial_day", "ob_pay", "ob_total"):
        if k in st:
            attrs[k] = st[k]
    shift = st.get("shift")
    if isinstance(shift, dict):
        attrs["shift_code"]  = shift.get("code")
        attrs["shift_label"] = shift.get("label")
        attrs["shift_color"] = shift.get("color")
    return attrs


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
    days = _get_day_list(sw)
    total = sum(_day_hours(d) for d in days if _day_is_working(d))
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
    today_str = date.today().isoformat()
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
    days = _get_day_list(sy)
    total = sum(_day_hours(d) for d in days if _day_is_working(d))
    return round(total, 2) if total else None


def _year_attrs(data: dict) -> dict[str, Any]:
    sy = data.get(DATA_SCHEDULE_YEAR)
    if not isinstance(sy, dict):
        return {}
    return {
        k: v for k, v in sy.items()
        if k in ("year", "total_hours", "total_shifts", "num_shifts")
    }


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


def _vacation_remaining(data: dict) -> float | None:
    vb = data.get(DATA_VACATION_BALANCE)
    if not vb:
        return None
    return (
        vb.get("remaining") or vb.get("remaining_days")
        or vb.get("balance") or vb.get("days_remaining")
    )


def _vacation_used(data: dict) -> float | None:
    vb = data.get(DATA_VACATION_BALANCE)
    if not vb:
        return None
    return vb.get("used") or vb.get("used_days") or vb.get("days_used")


def _vacation_total(data: dict) -> float | None:
    vb = data.get(DATA_VACATION_BALANCE)
    if not vb:
        return None
    return vb.get("total") or vb.get("total_days") or vb.get("entitled_days")


def _vacation_attrs(data: dict) -> dict[str, Any]:
    return {k: v for k, v in (data.get(DATA_VACATION_BALANCE) or {}).items()}


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
    return _pay_float(data, "brutto_pay", "gross_pay", "total_pay", "gross", "total", "amount")


def _pay_netto(data: dict) -> float | None:
    return _pay_float(data, "netto_pay", "net_pay", "netto")


def _pay_hours(data: dict) -> float | None:
    return _pay_float(data, "total_hours", "hours", "worked_hours")


def _pay_shifts(data: dict) -> int | None:
    return _pay_int(data, "num_shifts", "shifts")


def _pay_oncall(data: dict) -> float | None:
    return _pay_float(data, "oncall_pay")


def _pay_oncall_hours(data: dict) -> float | None:
    return _pay_float(data, "oncall_hours")


def _pay_overtime(data: dict) -> float | None:
    return _pay_float(data, "ot_pay", "overtime_pay")


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
    keep = (
        "year", "month", "total_hours", "num_shifts",
        "ob_pay", "ob_hours",
        "oncall_pay", "oncall_hours",
        "ot_pay",
        "absence_deduction", "absence_hours",
        "sick_days", "sick_hours",
        "vab_days", "vab_hours",
        "leave_days", "leave_hours",
        "brutto_pay", "netto_pay",
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

SENSOR_DESCRIPTIONS: tuple[PeriodicalSensorDescription, ...] = (

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
        attr_fn=_today_shift_attrs,
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
        attr_fn=_status_attrs,
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
        attr_fn=_year_attrs,
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
        attr_fn=_year_attrs,
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
        attr_fn=_next_shift_attrs,
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
        icon="mdi:calendar-tomorrow",
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
        attr_fn=_tomorrow_shift_attrs,
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
