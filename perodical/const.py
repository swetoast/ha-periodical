"""Constants for the Periodical integration."""
from datetime import timedelta

DOMAIN = "periodical"
CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_USER_ID = "user_id"
CONF_USER_NAME = "user_name"

DEFAULT_BASE_URL = "https://periodical.com/api/v1"

# How often to refresh all data
SCAN_INTERVAL = timedelta(minutes=15)

# Coordinator data keys
DATA_ME                  = "me"
DATA_STATUS              = "status"
DATA_SCHEDULE_TODAY      = "schedule_today"
DATA_SCHEDULE_WEEK       = "schedule_week"        # ISO week containing today
DATA_SCHEDULE_MONTH      = "schedule_month"
DATA_SCHEDULE_YEAR       = "schedule_year"        # full current year
DATA_NEXT_SHIFT          = "next_shift"
DATA_NEXT_SHIFT_TOMORROW = "next_shift_tomorrow"  # always tomorrow's calendar date
DATA_VACATION_BALANCE    = "vacation_balance"
DATA_PAY_MONTH           = "pay_month"
DATA_ABSENCES            = "absences"