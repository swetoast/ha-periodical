"""Constants for the Periodical integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "periodical"

CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_USER_ID = "user_id"
CONF_USER_NAME = "user_name"

DEFAULT_BASE_URL = "https://periodical.com/api/v1"
SCAN_INTERVAL = timedelta(minutes=15)

DATA_ME = "me"
DATA_STATUS = "status"
DATA_SCHEDULE_TODAY = "schedule_today"
DATA_SCHEDULE_WEEK = "schedule_week"
DATA_SCHEDULE_MONTH = "schedule_month"
DATA_SCHEDULE_YEAR = "schedule_year"
DATA_NEXT_SHIFT = "next_shift"
DATA_NEXT_SHIFT_TOMORROW = "next_shift_tomorrow"
DATA_VACATION_BALANCE = "vacation_balance"
DATA_PAY_MONTH = "pay_month"
DATA_ABSENCES = "absences"
DATA_API_HEALTH = "api_health"
