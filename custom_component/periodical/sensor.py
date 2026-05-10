"""Sensor platform for Periodical."""
from __future__ import annotations

import logging
import time as _time_mod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_USER_ID, CONF_USER_NAME, DATA_ABSENCES, DATA_NEXT_SHIFT, DATA_NEXT_SHIFT_TOMORROW, DATA_PAY_MONTH, DATA_SCHEDULE_MONTH, DATA_SCHEDULE_TODAY, DATA_SCHEDULE_WEEK, DATA_SCHEDULE_YEAR, DATA_STATUS, DATA_VACATION_BALANCE, DOMAIN
from .coordinator import PeriodicalCoordinator
from .helpers import clean_attrs, coworker_name, day_hours, day_is_working, normalize_absence_items, normalize_pay, normalize_schedule_days, normalize_shift, schedule_code, schedule_code_icon, schedule_code_name

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PeriodicalSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attr_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _local_tz() -> timezone:
    offset_sec = -(_time_mod.altzone if _time_mod.daylight else _time_mod.timezone)
    return timezone(timedelta(seconds=offset_sec))


def _hhmm_to_datetime(val: str | None, base_date: date | None = None) -> datetime | None:
    if not val:
        return None
    try:
        hour, minute = val.split(":")[:2]
        return datetime.combine(base_date or date.today(), time(int(hour), int(minute)), tzinfo=_local_tz())
    except (IndexError, ValueError, TypeError):
        return None


def _parse_iso_date(val: str | None) -> date | None:
    if not val:
        return None
    try:
        return date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _shift_attrs(value: dict[str, Any]) -> dict[str, Any]:
    shift = normalize_shift(value)
    code = schedule_code(value)
    return clean_attrs({"schedule_code": code, "schedule_name": schedule_code_name(code), "schedule_icon": schedule_code_icon(code), "shift_code": shift.get("code"), "shift_label": shift.get("label"), "shift_color": shift.get("color"), "start_time": shift.get("start_time"), "end_time": shift.get("end_time")})


def _shift_attrs_from_ns(ns: dict[str, Any]) -> dict[str, Any]:
    attrs = {"date": ns.get("date"), "days_from_today": ns.get("days_from_today"), "rotation_week": ns.get("rotation_week")}
    shift = ns.get("shift")
    if isinstance(shift, dict):
        attrs.update(_shift_attrs(shift))
    return clean_attrs(attrs)


def _get_today_shift(data: dict[str, Any]) -> dict[str, Any] | None:
    for key in (DATA_STATUS, DATA_SCHEDULE_TODAY):
        day = data.get(key)
        if isinstance(day, dict):
            shift = day.get("shift")
            if isinstance(shift, dict) and (shift.get("start_time") or schedule_code(shift)):
                return shift
            if day.get("start_time") or schedule_code(day):
                return day
    return None


def _today_start(data: dict[str, Any]) -> datetime | None:
    shift = _get_today_shift(data)
    return _hhmm_to_datetime(shift.get("start_time") if shift else None)


def _today_end(data: dict[str, Any]) -> datetime | None:
    shift = _get_today_shift(data)
    if not shift:
        return None
    end_dt = _hhmm_to_datetime(shift.get("end_time"))
    start_dt = _hhmm_to_datetime(shift.get("start_time"))
    if end_dt and start_dt and end_dt < start_dt:
        end_dt += timedelta(days=1)
    return end_dt


def _today_shift_attrs(data: dict[str, Any]) -> dict[str, Any]:
    shift = _get_today_shift(data)
    return _shift_attrs(shift) if shift else {}


def _get_coworkers(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in (DATA_STATUS, DATA_SCHEDULE_TODAY):
        day = data.get(key)
        if isinstance(day, dict):
            coworkers = day.get("coworkers") or day.get("co_workers") or []
            if isinstance(coworkers, list):
                return [item for item in coworkers if isinstance(item, dict)]
    return []


def _coworker_attrs(coworker: dict[str, Any]) -> dict[str, Any]:
    shift = normalize_shift(coworker)
    code = schedule_code(coworker)
    return clean_attrs({"name": coworker_name(coworker), "schedule_code": code, "schedule_name": schedule_code_name(code), "schedule_icon": schedule_code_icon(code), "is_on_call": code == "OC", "shift_code": shift.get("code") or coworker.get("shift_code"), "shift_label": shift.get("label") or coworker.get("shift_label"), "start_time": shift.get("start_time") or coworker.get("start_time"), "end_time": shift.get("end_time") or coworker.get("end_time")})


def _today_coworkers_count(data: dict[str, Any]) -> int:
    return len(_get_coworkers(data))


def _today_coworkers_attrs(data: dict[str, Any]) -> dict[str, Any]:
    coworkers = [_coworker_attrs(coworker) for coworker in _get_coworkers(data)]
    on_call = [coworker for coworker in coworkers if coworker.get("is_on_call")]
    return {"co_workers": coworkers, "on_call": on_call, "on_call_names": [coworker["name"] for coworker in on_call if coworker.get("name")]}


def _oncall_today(data: dict[str, Any]) -> str | None:
    names = _today_coworkers_attrs(data).get("on_call_names", [])
    return ", ".join(names) if names else None


def _oncall_today_attrs(data: dict[str, Any]) -> dict[str, Any]:
    attrs = _today_coworkers_attrs(data)
    return {"on_call": attrs.get("on_call", []), "on_call_names": attrs.get("on_call_names", [])}


def _status_today(data: dict[str, Any]) -> str | None:
    status = data.get(DATA_STATUS)
    if isinstance(status, dict):
        code = schedule_code(status)
        return schedule_code_name(code) or status.get("status")
    return None


def _ob_total(data: dict[str, Any]) -> float | None:
    status = data.get(DATA_STATUS) or {}
    if not isinstance(status, dict):
        return None
    value = status.get("ob_total") or status.get("ob") or status.get("ob_supplement")
    try:
        return round(float(value), 2) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rotation_week(data: dict[str, Any]) -> int | None:
    status = data.get(DATA_STATUS) or {}
    if not isinstance(status, dict):
        return None
    value = status.get("rotation_week")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _status_attrs(data: dict[str, Any]) -> dict[str, Any]:
    status = data.get(DATA_STATUS) or {}
    if not isinstance(status, dict):
        return {}
    attrs = {key: status[key] for key in ("status", "rotation_week", "overtime", "partial_day", "ob_pay", "ob_total") if key in status}
    attrs.update(_shift_attrs(status))
    return clean_attrs(attrs)


def _week_shifts_count(data: dict[str, Any]) -> int | None:
    schedule = data.get(DATA_SCHEDULE_WEEK)
    if schedule is None:
        return None
    return sum(1 for day in normalize_schedule_days(schedule) if day_is_working(day))


def _week_hours(data: dict[str, Any]) -> float | None:
    schedule = data.get(DATA_SCHEDULE_WEEK)
    if schedule is None:
        return None
    total = sum(day_hours(day) for day in normalize_schedule_days(schedule) if day_is_working(day))
    return round(total, 2) if total else None


def _schedule_days_attrs(schedule: Any) -> dict[str, Any]:
    days = []
    for day in normalize_schedule_days(schedule):
        shift = normalize_shift(day)
        code = schedule_code(day)
        item = clean_attrs({"date": day.get("date"), "status": day.get("status"), "schedule_code": code, "schedule_name": schedule_code_name(code), "schedule_icon": schedule_code_icon(code), "shift_code": shift.get("code") or day.get("shift_code"), "shift_label": shift.get("label") or day.get("shift_label"), "start_time": shift.get("start_time") or day.get("start_time"), "end_time": shift.get("end_time") or day.get("end_time")})
        coworkers = day.get("coworkers") or day.get("co_workers")
        if isinstance(coworkers, list):
            coworker_items = [_coworker_attrs(coworker) for coworker in coworkers if isinstance(coworker, dict)]
            item["co_workers"] = coworker_items
            item["on_call"] = [coworker for coworker in coworker_items if coworker.get("is_on_call")]
        if item:
            days.append(item)
    return {"days": days}


def _week_attrs(data: dict[str, Any]) -> dict[str, Any]:
    schedule = data.get(DATA_SCHEDULE_WEEK)
    return _schedule_days_attrs(schedule) if schedule is not None else {}


def _year_total_shifts(data: dict[str, Any]) -> int | None:
    schedule = data.get(DATA_SCHEDULE_YEAR)
    if schedule is None:
        return None
    if isinstance(schedule, dict):
        for key in ("total_shifts", "num_shifts", "shift_count"):
            value = schedule.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
    total = sum(1 for day in normalize_schedule_days(schedule) if day_is_working(day))
    return total or None


def _year_remaining_shifts(data: dict[str, Any]) -> int | None:
    schedule = data.get(DATA_SCHEDULE_YEAR)
    if schedule is None:
        return None
    today_str = date.today().isoformat()
    total = sum(1 for day in normalize_schedule_days(schedule) if day_is_working(day) and (day.get("date") or "") >= today_str)
    return total or None


def _year_total_hours(data: dict[str, Any]) -> float | None:
    schedule = data.get(DATA_SCHEDULE_YEAR)
    if schedule is None:
        return None
    if isinstance(schedule, dict):
        for key in ("total_hours", "hours"):
            value = schedule.get(key)
            if value is not None:
                try:
                    return round(float(value), 2)
                except (TypeError, ValueError):
                    pass
    total = sum(day_hours(day) for day in normalize_schedule_days(schedule) if day_is_working(day))
    return round(total, 2) if total else None


def _year_attrs(data: dict[str, Any]) -> dict[str, Any]:
    schedule = data.get(DATA_SCHEDULE_YEAR)
    return {key: value for key, value in schedule.items() if key in ("year", "total_hours", "total_shifts", "num_shifts")} if isinstance(schedule, dict) else {}


def _next_shift_date(data: dict[str, Any]) -> date | None:
    item = data.get(DATA_NEXT_SHIFT)
    return _parse_iso_date(item.get("date")) if isinstance(item, dict) else None


def _next_shift_start(data: dict[str, Any]) -> str | None:
    item = data.get(DATA_NEXT_SHIFT)
    return (item.get("shift") or {}).get("start_time") if isinstance(item, dict) else None


def _next_shift_end(data: dict[str, Any]) -> str | None:
    item = data.get(DATA_NEXT_SHIFT)
    return (item.get("shift") or {}).get("end_time") if isinstance(item, dict) else None


def _next_shift_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return _shift_attrs_from_ns(data.get(DATA_NEXT_SHIFT) or {})


def _tomorrow_shift_date(data: dict[str, Any]) -> date | None:
    item = data.get(DATA_NEXT_SHIFT_TOMORROW)
    return _parse_iso_date(item.get("date")) if isinstance(item, dict) else None


def _tomorrow_shift_start(data: dict[str, Any]) -> str | None:
    item = data.get(DATA_NEXT_SHIFT_TOMORROW)
    return (item.get("shift") or {}).get("start_time") if isinstance(item, dict) else None


def _tomorrow_shift_end(data: dict[str, Any]) -> str | None:
    item = data.get(DATA_NEXT_SHIFT_TOMORROW)
    return (item.get("shift") or {}).get("end_time") if isinstance(item, dict) else None


def _tomorrow_shift_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return _shift_attrs_from_ns(data.get(DATA_NEXT_SHIFT_TOMORROW) or {})


def _vacation_remaining(data: dict[str, Any]) -> float | None:
    vacation = data.get(DATA_VACATION_BALANCE)
    return (vacation.get("remaining") or vacation.get("remaining_days") or vacation.get("balance") or vacation.get("days_remaining")) if isinstance(vacation, dict) else None


def _vacation_used(data: dict[str, Any]) -> float | None:
    vacation = data.get(DATA_VACATION_BALANCE)
    return (vacation.get("used") or vacation.get("used_days") or vacation.get("days_used")) if isinstance(vacation, dict) else None


def _vacation_total(data: dict[str, Any]) -> float | None:
    vacation = data.get(DATA_VACATION_BALANCE)
    return (vacation.get("total") or vacation.get("total_days") or vacation.get("entitled_days")) if isinstance(vacation, dict) else None


def _vacation_attrs(data: dict[str, Any]) -> dict[str, Any]:
    vacation = data.get(DATA_VACATION_BALANCE)
    return dict(vacation) if isinstance(vacation, dict) else {}


def _pay_float(data: dict[str, Any], *keys: str) -> float | None:
    pay = normalize_pay(data.get(DATA_PAY_MONTH))
    for key in keys:
        value = pay.get(key)
        if value is not None:
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                pass
    return None


def _pay_int(data: dict[str, Any], *keys: str) -> int | None:
    pay = normalize_pay(data.get(DATA_PAY_MONTH))
    for key in keys:
        value = pay.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def _pay_brutto(data: dict[str, Any]) -> float | None:
    return _pay_float(data, "brutto_pay", "gross_pay", "total_pay", "gross", "total", "amount")


def _pay_netto(data: dict[str, Any]) -> float | None:
    return _pay_float(data, "netto_pay", "net_pay", "netto")


def _pay_hours(data: dict[str, Any]) -> float | None:
    return _pay_float(data, "total_hours", "hours", "worked_hours")


def _pay_shifts(data: dict[str, Any]) -> int | None:
    return _pay_int(data, "num_shifts", "shifts")


def _pay_oncall(data: dict[str, Any]) -> float | None:
    return _pay_float(data, "oncall_pay")


def _pay_oncall_hours(data: dict[str, Any]) -> float | None:
    return _pay_float(data, "oncall_hours")


def _pay_overtime(data: dict[str, Any]) -> float | None:
    return _pay_float(data, "ot_pay", "overtime_pay")


def _pay_sick_days(data: dict[str, Any]) -> int | None:
    return _pay_int(data, "sick_days")


def _pay_sick_hours(data: dict[str, Any]) -> float | None:
    return _pay_float(data, "sick_hours")


def _pay_vab_days(data: dict[str, Any]) -> int | None:
    return _pay_int(data, "vab_days")


def _pay_leave_days(data: dict[str, Any]) -> int | None:
    return _pay_int(data, "leave_days")


def _pay_attrs(data: dict[str, Any]) -> dict[str, Any]:
    pay = normalize_pay(data.get(DATA_PAY_MONTH))
    keep = ("year", "month", "total_hours", "num_shifts", "ob_pay", "ob_hours", "oncall_pay", "oncall_hours", "ot_pay", "overtime_pay", "absence_deduction", "absence_hours", "sick_days", "sick_hours", "vab_days", "vab_hours", "leave_days", "leave_hours", "brutto_pay", "gross_pay", "netto_pay", "net_pay")
    return {key: pay[key] for key in keep if key in pay}


def _absences_count(data: dict[str, Any]) -> int:
    return len(normalize_absence_items(data.get(DATA_ABSENCES)))


def _absences_attrs(data: dict[str, Any]) -> dict[str, Any]:
    absences = data.get(DATA_ABSENCES)
    if isinstance(absences, list):
        return {"absences": absences}
    if isinstance(absences, dict):
        return dict(absences)
    return {}


def _schedule_month_working_days(data: dict[str, Any]) -> int | None:
    schedule = data.get(DATA_SCHEDULE_MONTH)
    if not schedule:
        return None
    if isinstance(schedule, dict):
        for key in ("working_days", "num_shifts", "shift_count"):
            value = schedule.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
    count = sum(1 for day in normalize_schedule_days(schedule) if day_is_working(day))
    return count or None


def _schedule_month_attrs(data: dict[str, Any]) -> dict[str, Any]:
    schedule = data.get(DATA_SCHEDULE_MONTH)
    if not isinstance(schedule, dict):
        return {}
    attrs = {key: schedule[key] for key in ("month", "year", "total_hours", "working_days", "days_off", "num_shifts", "shift_count") if key in schedule}
    attrs.update(_schedule_days_attrs(schedule))
    return attrs


SENSOR_DESCRIPTIONS: tuple[PeriodicalSensorDescription, ...] = (
    PeriodicalSensorDescription(key="shift_start_today", translation_key="shift_start_today", name="Shift Start Today", icon="mdi:clock-start", device_class=SensorDeviceClass.TIMESTAMP, value_fn=_today_start, attr_fn=_today_shift_attrs),
    PeriodicalSensorDescription(key="shift_end_today", translation_key="shift_end_today", name="Shift End Today", icon="mdi:clock-end", device_class=SensorDeviceClass.TIMESTAMP, value_fn=_today_end, attr_fn=_today_shift_attrs),
    PeriodicalSensorDescription(key="coworkers_today", translation_key="coworkers_today", name="Co-workers Today", icon="mdi:account-group", native_unit_of_measurement="people", state_class=SensorStateClass.MEASUREMENT, value_fn=_today_coworkers_count, attr_fn=_today_coworkers_attrs),
    PeriodicalSensorDescription(key="oncall_today", translation_key="oncall_today", name="On-call Today", icon="mdi:phone-alert", value_fn=_oncall_today, attr_fn=_oncall_today_attrs),
    PeriodicalSensorDescription(key="status_today", translation_key="status_today", name="Status Today", icon="mdi:information-outline", value_fn=_status_today, attr_fn=_status_attrs),
    PeriodicalSensorDescription(key="ob_today", translation_key="ob_today", name="OB Supplement Today", icon="mdi:cash-plus", native_unit_of_measurement="SEK", state_class=SensorStateClass.MEASUREMENT, value_fn=_ob_total, attr_fn=_status_attrs),
    PeriodicalSensorDescription(key="rotation_week", translation_key="rotation_week", name="Rotation Week", icon="mdi:rotate-right", state_class=SensorStateClass.MEASUREMENT, value_fn=_rotation_week),
    PeriodicalSensorDescription(key="shifts_this_week", translation_key="shifts_this_week", name="Shifts This Week", icon="mdi:calendar-week", native_unit_of_measurement="shifts", state_class=SensorStateClass.MEASUREMENT, value_fn=_week_shifts_count, attr_fn=_week_attrs),
    PeriodicalSensorDescription(key="hours_this_week", translation_key="hours_this_week", name="Hours This Week", icon="mdi:clock-outline", native_unit_of_measurement="h", state_class=SensorStateClass.MEASUREMENT, value_fn=_week_hours, attr_fn=_week_attrs),
    PeriodicalSensorDescription(key="working_days_month", translation_key="working_days_month", name="Working Days This Month", icon="mdi:calendar-month", native_unit_of_measurement="days", state_class=SensorStateClass.MEASUREMENT, value_fn=_schedule_month_working_days, attr_fn=_schedule_month_attrs),
    PeriodicalSensorDescription(key="shifts_this_year", translation_key="shifts_this_year", name="Shifts This Year", icon="mdi:calendar-blank-multiple", native_unit_of_measurement="shifts", state_class=SensorStateClass.MEASUREMENT, value_fn=_year_total_shifts, attr_fn=_year_attrs),
    PeriodicalSensorDescription(key="shifts_remaining_year", translation_key="shifts_remaining_year", name="Shifts Remaining This Year", icon="mdi:calendar-arrow-right", native_unit_of_measurement="shifts", state_class=SensorStateClass.MEASUREMENT, value_fn=_year_remaining_shifts),
    PeriodicalSensorDescription(key="hours_this_year", translation_key="hours_this_year", name="Hours This Year", icon="mdi:clock-check-outline", native_unit_of_measurement="h", state_class=SensorStateClass.MEASUREMENT, value_fn=_year_total_hours, attr_fn=_year_attrs),
    PeriodicalSensorDescription(key="next_shift_date", translation_key="next_shift_date", name="Next Shift Date", icon="mdi:calendar-arrow-right", device_class=SensorDeviceClass.DATE, value_fn=_next_shift_date, attr_fn=_next_shift_attrs),
    PeriodicalSensorDescription(key="next_shift_start", translation_key="next_shift_start", name="Next Shift Start", icon="mdi:clock-start", value_fn=_next_shift_start, attr_fn=_next_shift_attrs),
    PeriodicalSensorDescription(key="next_shift_end", translation_key="next_shift_end", name="Next Shift End", icon="mdi:clock-end", value_fn=_next_shift_end),
    PeriodicalSensorDescription(key="tomorrow_shift_date", translation_key="tomorrow_shift_date", name="Tomorrow Shift Date", icon="mdi:account-clock", device_class=SensorDeviceClass.DATE, value_fn=_tomorrow_shift_date, attr_fn=_tomorrow_shift_attrs),
    PeriodicalSensorDescription(key="tomorrow_shift_start", translation_key="tomorrow_shift_start", name="Tomorrow Shift Start", icon="mdi:clock-start", value_fn=_tomorrow_shift_start, attr_fn=_tomorrow_shift_attrs),
    PeriodicalSensorDescription(key="tomorrow_shift_end", translation_key="tomorrow_shift_end", name="Tomorrow Shift End", icon="mdi:clock-end", value_fn=_tomorrow_shift_end),
    PeriodicalSensorDescription(key="vacation_remaining", translation_key="vacation_remaining", name="Vacation Days Remaining", icon="mdi:beach", native_unit_of_measurement="days", state_class=SensorStateClass.MEASUREMENT, value_fn=_vacation_remaining, attr_fn=_vacation_attrs),
    PeriodicalSensorDescription(key="vacation_used", translation_key="vacation_used", name="Vacation Days Used", icon="mdi:umbrella-beach", native_unit_of_measurement="days", state_class=SensorStateClass.TOTAL_INCREASING, value_fn=_vacation_used),
    PeriodicalSensorDescription(key="vacation_total", translation_key="vacation_total", name="Vacation Days Total", icon="mdi:calendar-check", native_unit_of_measurement="days", value_fn=_vacation_total),
    PeriodicalSensorDescription(key="pay_month_gross", translation_key="pay_month_gross", name="Monthly Pay (Gross)", icon="mdi:currency-usd", native_unit_of_measurement="SEK", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_brutto, attr_fn=_pay_attrs),
    PeriodicalSensorDescription(key="pay_month_netto", translation_key="pay_month_netto", name="Monthly Pay (Netto)", icon="mdi:currency-usd", native_unit_of_measurement="SEK", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_netto),
    PeriodicalSensorDescription(key="pay_month_hours", translation_key="pay_month_hours", name="Monthly Hours Worked", icon="mdi:timer-outline", native_unit_of_measurement="h", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_hours),
    PeriodicalSensorDescription(key="pay_month_shifts", translation_key="pay_month_shifts", name="Monthly Shifts", icon="mdi:calendar-clock", native_unit_of_measurement="shifts", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_shifts),
    PeriodicalSensorDescription(key="pay_oncall_month", translation_key="pay_oncall_month", name="Monthly On-call Pay", icon="mdi:phone-clock", native_unit_of_measurement="SEK", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_oncall),
    PeriodicalSensorDescription(key="pay_oncall_hours_month", translation_key="pay_oncall_hours_month", name="Monthly On-call Hours", icon="mdi:phone-clock", native_unit_of_measurement="h", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_oncall_hours),
    PeriodicalSensorDescription(key="pay_overtime_month", translation_key="pay_overtime_month", name="Monthly Overtime Pay", icon="mdi:timer-plus-outline", native_unit_of_measurement="SEK", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_overtime),
    PeriodicalSensorDescription(key="pay_sick_days_month", translation_key="pay_sick_days_month", name="Monthly Sick Days", icon="mdi:emoticon-sick-outline", native_unit_of_measurement="days", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_sick_days),
    PeriodicalSensorDescription(key="pay_sick_hours_month", translation_key="pay_sick_hours_month", name="Monthly Sick Hours", icon="mdi:emoticon-sick-outline", native_unit_of_measurement="h", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_sick_hours),
    PeriodicalSensorDescription(key="pay_vab_days_month", translation_key="pay_vab_days_month", name="Monthly VAB Days", icon="mdi:baby-face-outline", native_unit_of_measurement="days", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_vab_days),
    PeriodicalSensorDescription(key="pay_leave_days_month", translation_key="pay_leave_days_month", name="Monthly Leave Days", icon="mdi:calendar-minus", native_unit_of_measurement="days", state_class=SensorStateClass.MEASUREMENT, value_fn=_pay_leave_days),
    PeriodicalSensorDescription(key="absences_count", translation_key="absences_count", name="Absences This Year", icon="mdi:calendar-remove", native_unit_of_measurement="absences", state_class=SensorStateClass.MEASUREMENT, value_fn=_absences_count, attr_fn=_absences_attrs),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: PeriodicalCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(PeriodicalSensor(coordinator, entry, description) for description in SENSOR_DESCRIPTIONS)


class PeriodicalSensor(CoordinatorEntity[PeriodicalCoordinator], SensorEntity):
    entity_description: PeriodicalSensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: PeriodicalCoordinator, entry: ConfigEntry, description: PeriodicalSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        user_name = entry.data.get(CONF_USER_NAME, "Periodical")
        user_id = entry.data[CONF_USER_ID]
        self._attr_unique_id = f"{DOMAIN}_{user_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, str(user_id))}, name=user_name, manufacturer="Periodical", model="Periodical API")

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except Exception:
            _LOGGER.debug("Error extracting value for %s", self.entity_description.key, exc_info=True)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None or self.entity_description.attr_fn is None:
            return {}
        try:
            return self.entity_description.attr_fn(self.coordinator.data) or {}
        except Exception:
            _LOGGER.debug("Error extracting attributes for %s", self.entity_description.key, exc_info=True)
            return {}
