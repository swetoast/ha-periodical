# Periodical Home Assistant Integration

Custom Home Assistant integration for connecting to a Periodical API server and exposing shift schedule, working status, absence, vacation and monthly pay data as Home Assistant entities.

Upstream Periodical application:

https://github.com/KalleL94/Periodical

## Features

- Config flow setup from the Home Assistant UI
- Bearer token authentication
- Cloud polling through Home Assistant `DataUpdateCoordinator`
- Sensors for:
  - today's shift start and end
  - co-workers today
  - today's status
  - OB supplement today
  - rotation week
  - weekly shift and hour totals
  - monthly working days
  - yearly shift/hour totals
  - next shift
  - tomorrow's shift
  - vacation balance
  - monthly pay summary
  - absence count
- Binary sensors for:
  - working today
  - absent today
- Service helpers for fetching schedule and pay data manually

## Installation

Copy the integration folder to your Home Assistant custom components directory:

```text
config/custom_components/periodical/
```

Expected structure:

```text
custom_components/periodical/
├── __init__.py
├── api.py
├── binary_sensor.py
├── config_flow.py
├── const.py
├── coordinator.py
├── manifest.json
├── sensor.py
├── services.py
└── strings.json
```

Restart Home Assistant after copying the files.

## Configuration

Add the integration from Home Assistant:

```text
Settings → Devices & services → Add integration → Periodical
```

Required fields:

- `API Key` - your Periodical bearer token
- `API Base URL` - default: `https://periodical.kakanweb.com/api/v1`

The config flow validates the API key by calling `/me`.

## API Authentication

Requests are sent with:

```http
Authorization: Bearer YOUR_API_KEY
Accept: application/json
```

Authentication failures return:

- `401 Unauthorized` - invalid or missing API key
- `403 Forbidden` - authenticated but access denied

## Polling

The integration refreshes data every 15 minutes by default.

This is controlled in `const.py`:

```python
SCAN_INTERVAL = timedelta(minutes=15)
```

## Entities

### Binary sensors

| Entity | Description |
|---|---|
| `binary_sensor.periodical_working_today` | On when today's status is `working` |
| `binary_sensor.periodical_absent_today` | On when an absence covers today's date |

### Sensors

Common sensors exposed by the integration include:

| Sensor | Description |
|---|---|
| Shift Start Today | Start time for today's shift |
| Shift End Today | End time for today's shift |
| Co-workers Today | Number of co-workers today |
| Status Today | Current day status |
| OB Supplement Today | OB amount for today |
| Rotation Week | Current rotation week |
| Shifts This Week | Number of shifts this week |
| Hours This Week | Total working hours this week |
| Working Days This Month | Working days this month |
| Shifts This Year | Total shifts this year |
| Shifts Remaining This Year | Remaining shifts this year |
| Hours This Year | Total working hours this year |
| Next Shift Date | Date of next upcoming shift |
| Next Shift Start | Start time of next upcoming shift |
| Next Shift End | End time of next upcoming shift |
| Tomorrow Shift Date | Tomorrow shift date |
| Tomorrow Shift Start | Tomorrow shift start time |
| Tomorrow Shift End | Tomorrow shift end time |
| Vacation Days Remaining | Remaining vacation days |
| Vacation Days Used | Used vacation days |
| Vacation Days Total | Total vacation days |
| Monthly Pay Gross | Monthly gross pay |
| Monthly Pay Netto | Monthly net pay |
| Monthly Hours Worked | Monthly worked hours |
| Monthly Shifts | Monthly shift count |
| Monthly On-call Pay | Monthly on-call pay |
| Monthly On-call Hours | Monthly on-call hours |
| Monthly Overtime Pay | Monthly overtime pay |
| Monthly Sick Days | Monthly sick days |
| Monthly Sick Hours | Monthly sick hours |
| Monthly VAB Days | Monthly VAB days |
| Monthly Leave Days | Monthly leave days |
| Absences This Year | Absence count for the year |

## Services

The integration registers extra Home Assistant services under the `periodical` domain.

### `periodical.get_schedule_date`

Fetch schedule for a specific date.

```yaml
date: "2026-05-05"
```

### `periodical.get_schedule_week`

Fetch schedule week containing the selected date.

```yaml
date: "2026-05-05"
```

### `periodical.get_schedule_range`

Fetch schedule for a date range.

```yaml
from_date: "2026-05-01"
to_date: "2026-05-31"
```

### `periodical.get_pay_month`

Fetch monthly pay summary.

```yaml
year: 2026
month: 5
```

### `periodical.get_vacation_balance`

Fetch vacation balance.

```yaml
year: 2026
```

Service results are fired as Home Assistant events, for example:

```text
periodical_schedule_date
periodical_schedule_week
periodical_schedule_range
periodical_pay_month
periodical_vacation_balance
```

## API Endpoints Used

The integration uses these Periodical API endpoints:

```text
GET /me
GET /users
GET /users/{user_id}/status
GET /users/{user_id}/schedule/today
GET /users/{user_id}/schedule/month
GET /users/{user_id}/schedule/year
GET /users/{user_id}/schedule/week/{date}
GET /users/{user_id}/schedule
GET /users/{user_id}/schedule/{date}
GET /users/{user_id}/pay/month
GET /users/{user_id}/vacation/balance
GET /users/{user_id}/absences
GET /users/{user_id}/next-shift
```

## HTTP Retry and Timeout Policy

Recommended API behavior:

- Request timeout: `30s`
- Total attempts: `3`
- Retries after first failure: `2`

Retryable HTTP statuses:

```text
408 Request Timeout
421 Misdirected Request
425 Too Early
429 Too Many Requests
500 Internal Server Error
502 Bad Gateway
503 Service Unavailable
504 Gateway Timeout
520 Unknown Error
521 Web Server Is Down
522 Connection Timed Out
523 Origin Is Unreachable
524 A Timeout Occurred
```

Fail immediately:

```text
400 Bad Request
401 Unauthorized
403 Forbidden
404 Not Found
405 Method Not Allowed
409 Conflict
410 Gone
422 Unprocessable Entity
```

Connection errors and timeouts should retry until attempts are exhausted.

## Troubleshooting

### Invalid API key

If setup fails with invalid authentication, verify that the bearer token is correct and that the Periodical API server accepts it.

### Cannot connect

Check:

- API base URL
- DNS/network access from Home Assistant
- reverse proxy configuration
- TLS certificate validity
- Periodical backend status

### Entities unavailable

Check Home Assistant logs for errors from:

```text
custom_components.periodical.api
custom_components.periodical.coordinator
```

### Wrong or missing schedule data

Verify the API response directly against the Periodical backend endpoint for the affected user and date.

## Development Notes

- Domain: `periodical`
- IoT class: `cloud_polling`
- Config flow: enabled
- No external Python package requirements are currently declared
- Integration depends on Home Assistant's shared aiohttp client session
- Data refresh is coordinated through `PeriodicalCoordinator`

## License

This Home Assistant custom component should follow the license chosen by its repository owner.

The upstream Periodical application is maintained separately at:

https://github.com/KalleL94/Periodical
