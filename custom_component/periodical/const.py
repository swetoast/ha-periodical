"""Constants for the Periodical integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "periodical"

CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_USER_ID = "user_id"
CONF_USER_NAME = "user_name"
CONF_ENABLED_ENDPOINTS = "enabled_endpoints"
CONF_UPDATE_INTERVAL_MINUTES = "update_interval_minutes"
CONF_REQUEST_TIMEOUT_SECONDS = "request_timeout_seconds"
CONF_RETRY_ATTEMPTS = "retry_attempts"
CONF_MAX_CONCURRENT_REQUESTS = "max_concurrent_requests"

DEFAULT_BASE_URL = "https://periodical.com/api/v1"
DEFAULT_UPDATE_INTERVAL_MINUTES = 15
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_MAX_CONCURRENT_REQUESTS = 3
SCAN_INTERVAL = timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MINUTES)

SERVICE_REFRESH = "refresh"
SERVICE_REFRESH_ENDPOINT = "refresh_endpoint"
SERVICE_GET_SCHEDULE_DATE = "get_schedule_date"
SERVICE_GET_SCHEDULE_WEEK = "get_schedule_week"
SERVICE_GET_SCHEDULE_RANGE = "get_schedule_range"
SERVICE_GET_PAY_MONTH = "get_pay_month"
SERVICE_GET_VACATION_BALANCE = "get_vacation_balance"

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

ALL_ENDPOINT_KEYS = (
    DATA_STATUS,
    DATA_SCHEDULE_TODAY,
    DATA_NEXT_SHIFT,
    DATA_SCHEDULE_WEEK,
    DATA_NEXT_SHIFT_TOMORROW,
    DATA_ABSENCES,
    DATA_SCHEDULE_MONTH,
    DATA_VACATION_BALANCE,
    DATA_ME,
    DATA_SCHEDULE_YEAR,
    DATA_PAY_MONTH,
)
