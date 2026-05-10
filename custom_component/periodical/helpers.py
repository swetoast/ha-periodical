"""Schema normalization helpers for Periodical."""
from __future__ import annotations

from typing import Any

SCHEDULE_CODE_MAP: dict[str, dict[str, str]] = {
    "N1": {"name": "Dayshift", "icon": "mdi:white-balance-sunny"},
    "N2": {"name": "Eveningshift", "icon": "mdi:weather-sunset"},
    "N3": {"name": "Nightshift", "icon": "mdi:weather-night"},
    "OC": {"name": "On call", "icon": "mdi:phone-alert"},
    "OT": {"name": "Overtime", "icon": "mdi:clock-plus-outline"},
    "VAB": {"name": "Care of child", "icon": "mdi:baby-face-outline"},
    "SEM": {"name": "Vacation", "icon": "mdi:beach"},
    "LEAVE": {"name": "On leave", "icon": "mdi:calendar-minus"},
    "SICK": {"name": "Sick", "icon": "mdi:emoticon-sick-outline"},
    "OFF": {"name": "Day off", "icon": "mdi:calendar-remove"},
}
NON_WORKING_CODES = {"OFF", "SEM", "LEAVE", "SICK", "VAB"}


def clean_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attrs.items() if value is not None}


def normalize_schedule_days(data_block: Any) -> list[dict[str, Any]]:
    if isinstance(data_block, list):
        return [item for item in data_block if isinstance(item, dict)]
    if not isinstance(data_block, dict):
        return []
    for key in ("days", "schedule", "shifts", "items", "data"):
        value = data_block.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    weeks = data_block.get("weeks")
    if isinstance(weeks, list):
        days: list[dict[str, Any]] = []
        for week in weeks:
            days.extend(normalize_schedule_days(week))
        return days
    return []


def normalize_shift(day: dict[str, Any]) -> dict[str, Any]:
    shift = day.get("shift")
    return shift if isinstance(shift, dict) else day


def schedule_code(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    shift = value.get("shift") if isinstance(value.get("shift"), dict) else value
    code = (
        shift.get("code")
        or shift.get("shift_code")
        or shift.get("schedule_code")
        or shift.get("type")
        or shift.get("status_code")
    )
    return str(code).upper() if code else None


def schedule_code_name(code: str | None) -> str | None:
    return SCHEDULE_CODE_MAP.get(code.upper(), {}).get("name") if code else None


def schedule_code_icon(code: str | None) -> str | None:
    return SCHEDULE_CODE_MAP.get(code.upper(), {}).get("icon") if code else None


def day_is_working(day: dict[str, Any]) -> bool:
    code = schedule_code(day)
    if code in NON_WORKING_CODES:
        return False
    status = day.get("status")
    if isinstance(status, str):
        return status.lower() == "working"
    shift = normalize_shift(day)
    return bool(shift.get("start_time"))


def day_hours(day: dict[str, Any]) -> float:
    if day.get("total_hours") is not None:
        try:
            return float(day["total_hours"])
        except (TypeError, ValueError):
            pass
    if schedule_code(day) in NON_WORKING_CODES:
        return 0.0
    shift = normalize_shift(day)
    start_str = shift.get("start_time")
    end_str = shift.get("end_time")
    if not start_str or not end_str:
        return 0.0
    try:
        sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
        eh, em = int(end_str.split(":")[0]), int(end_str.split(":")[1])
    except (IndexError, ValueError, AttributeError):
        return 0.0
    total = (eh * 60 + em) - (sh * 60 + sm)
    if total < 0:
        total += 24 * 60
    return round(total / 60, 2)


def normalize_absence_items(absences: Any) -> list[dict[str, Any]]:
    if isinstance(absences, list):
        return [item for item in absences if isinstance(item, dict)]
    if isinstance(absences, dict):
        for key in ("absences", "items", "data"):
            value = absences.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def normalize_pay(pay_month: Any) -> dict[str, Any]:
    return pay_month if isinstance(pay_month, dict) else {}


def coworker_name(coworker: dict[str, Any]) -> str | None:
    return (
        coworker.get("name")
        or coworker.get("full_name")
        or coworker.get("display_name")
        or coworker.get("username")
    )
