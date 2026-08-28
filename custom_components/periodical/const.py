"""Constants for the Periodical integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "periodical"
DATA_FRONTEND: Final = "frontend"
DATA_FRONTEND_LOCK: Final = "frontend_lock"

CONF_API_KEY: Final = "api_key"
CONF_BASE_URL: Final = "base_url"
CONF_USER_ID: Final = "user_id"
CONF_USER_NAME: Final = "user_name"

DEFAULT_BASE_URL: Final = "https://periodical.com/api/v1"
SCAN_INTERVAL: Final = timedelta(minutes=15)

# GET /users/{id}/schedule?from_date=&to_date= rejects spans wider than this.
MAX_SCHEDULE_RANGE_DAYS: Final = 70

# Coordinator data keys.
DATA_ME: Final = "me"
DATA_STATUS: Final = "status"
# One /schedule range call covering yesterday..tomorrow.  Yesterday is needed to
# anchor an overnight shift that is still running past midnight; tomorrow backs
# the "Tomorrow Shift *" sensors.  Its day objects have the same shape as
# /schedule/today, so it doubles as the fallback for today.
DATA_SCHEDULE_WINDOW: Final = "schedule_window"
DATA_SHIFTS: Final = "shifts"
DATA_SCHEDULE_WEEK: Final = "schedule_week"
DATA_SCHEDULE_MONTH: Final = "schedule_month"
DATA_SCHEDULE_YEAR: Final = "schedule_year"
DATA_NEXT_SHIFT: Final = "next_shift"
DATA_VACATION_BALANCE: Final = "vacation_balance"
DATA_PAY_MONTH: Final = "pay_month"
DATA_ABSENCES: Final = "absences"
DATA_API_HEALTH: Final = "api_health"

# Day statuses the API reports on /status and every /schedule/* day object.
STATUS_WORKING: Final = "working"
STATUS_OFF: Final = "off"
STATUS_UNKNOWN: Final = "unknown"

# Statuses that mean "scheduled, but not actually working".  The API still
# returns the rotation shift on these days (e.g. a vacation day keeps its N2
# 14:00-22:30 block), so every "today"/"tomorrow" shift sensor must consult the
# status before reporting shift times, or the integration shows a working day
# to somebody who is on holiday.
ABSENCE_STATUSES: Final = frozenset({"vacation", "sick", "vab", "leave", "parental"})

# Shift codes that are stand-by rather than worked hours.  On-call spans
# 00:00-00:00 with overnight=true, i.e. a full 24 h, and must not be added to
# worked-hour totals — payroll reports it separately as oncall_hours.
ONCALL_SHIFT_CODES: Final = frozenset({"OC"})
