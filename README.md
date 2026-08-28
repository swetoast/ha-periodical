# Periodical for Home Assistant

[![HACS Custom][hacs-shield]][hacs-url]
[![License][license-shield]][license-url]
[![Home Assistant][ha-shield]][ha-url]

Brings your Periodical shift rota into Home Assistant. Exposes today's shift, upcoming shifts, working status, absence, vacation balance and monthly pay as native entities you can automate against.

Built for rotating shift work: it understands overnight shifts that run past midnight, knows that an on-call block is stand-by rather than worked time, and reports you as absent on a booked holiday even though the rota still has a shift pencilled in for that day.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [How schedule data is interpreted](#how-schedule-data-is-interpreted)
- [Update strategy](#update-strategy)
- [Services](#services)
- [Automation examples](#automation-examples)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [Credits and license](#credits-and-license)

## Features

* UI config flow, no YAML required
* Automatic re-authentication when the API key is revoked or rotated
* 37 sensors and 4 binary sensors across schedule, absence, vacation and payroll
* Absence aware: vacation, sick, VAB and leave days report as absent instead of showing the rota shift
* Overnight aware: a night shift started at 22:00 yesterday is still the active shift at 02:00 today
* On-call aware: stand-by hours are tracked separately so they never inflate worked hour totals
* Tiered polling that matches how fast each endpoint actually changes, with forced refresh at day, month and year boundaries
* Five services for ad hoc lookups, returning data both as a response variable and as an event
* Multi account capable: add a second Periodical user as a second config entry
* English translations included, ready for further localisation

## Requirements

| | |
|---|---|
| Home Assistant | 2024.11.0 or newer |
| Periodical API | v1, reachable from your Home Assistant instance |
| Credentials | A personal API key (bearer token) |

The integration has no Python dependencies beyond what Home Assistant already ships.

## Installation

### HACS (recommended)

This repository is not in the HACS default list, so add it as a custom repository first.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=swetoast&repository=ha-periodical&category=integration)

Or do it by hand:

1. Open HACS in Home Assistant
2. Select the three dot menu, then **Custom repositories**
3. Add `https://github.com/swetoast/ha-periodical` with category **Integration**
4. Search for **Periodical** and select **Download**
5. Restart Home Assistant

The result should look like this:

```text
config/custom_components/periodical/
├── __init__.py
├── api.py
├── binary_sensor.py
├── config_flow.py
├── const.py
├── coordinator.py
├── entity.py
├── manifest.json
├── schedule.py
├── sensor.py
├── services.py
├── services.yaml
├── strings.json
├── frontend/
│   ├── __init__.py
│   └── periodical-card.js
└── translations/
    └── en.json
```

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=periodical)

Or navigate to **Settings** > **Devices & services** > **Add integration** and search for **Periodical**.

| Field | Required | Default | Notes |
|---|---|---|---|
| API Key | Yes | | Your personal bearer token from the Periodical web portal, under Settings > API |
| API Base URL | No | `https://periodical.com/api/v1` | Change only if you self host Periodical |

The key is validated immediately by calling `/me`. The numeric user id from that response identifies the account, so the entry survives a base URL change without duplicating itself.

### Re-authentication

If the API key is revoked or rotated, the integration raises a repair notification rather than quietly serving stale data. Open it, enter the new key, and everything reloads in place. A key belonging to a different Periodical user is rejected, since accepting it would silently repoint every entity at somebody else's rota.

### Multiple accounts

Add the integration again with a second API key. Each account gets its own device and its own set of entities. The first configured account keeps the short `sensor.periodical_*` entity ids; later accounts are scoped by user id, for example `sensor.periodical_14_status_today`.

## Entities

All entities live under a single device named after the Periodical account.

### Binary sensors

| Entity | Description |
|---|---|
| `binary_sensor.periodical_working_today` | On when today's status is `working` |
| `binary_sensor.periodical_absent_today` | On for vacation, sick, VAB, leave and parental days |
| `binary_sensor.periodical_api_problem` | Diagnostic. On when an endpoint failed, data is stale, or the circuit breaker is open |
| `binary_sensor.periodical_account_active` | Diagnostic. Reflects the `is_active` flag from `/me` |

### Sensors

**Today**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_shift_start_today` | timestamp | Start of the shift currently in effect, blank on absence and days off |
| `sensor.periodical_shift_end_today` | timestamp | End of that shift, rolled past midnight when it runs overnight |
| `sensor.periodical_status_today` | | `working`, `off`, `vacation`, `sick`, `vab`, `leave`, `parental` or `unknown` |
| `sensor.periodical_coworkers_today` | people | How many colleagues are scheduled today, with the roster in attributes |
| `sensor.periodical_ob_today` | SEK | Inconvenient hours supplement earned today |
| `sensor.periodical_rotation_week` | | Position in the rotation cycle |

**Upcoming**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_tomorrow_shift_date` | date | Tomorrow's date, blank unless tomorrow is actually worked |
| `sensor.periodical_tomorrow_shift_start` | | Tomorrow's start time as `HH:MM` |
| `sensor.periodical_tomorrow_shift_end` | | Tomorrow's end time as `HH:MM` |
| `sensor.periodical_next_shift_date` | date | Next working day, skipping days off and booked absence |
| `sensor.periodical_next_shift_start` | | Its start time as `HH:MM` |
| `sensor.periodical_next_shift_end` | | Its end time as `HH:MM` |

**Aggregates**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_shifts_this_week` | shifts | Worked shifts in the current ISO week, with a day by day breakdown in attributes |
| `sensor.periodical_hours_this_week` | h | Worked hours this week, on-call reported separately in attributes |
| `sensor.periodical_working_days_month` | days | Worked days in the current calendar month |
| `sensor.periodical_shifts_this_year` | shifts | Worked shifts across the year |
| `sensor.periodical_shifts_remaining_year` | shifts | Worked shifts still ahead of today |
| `sensor.periodical_hours_this_year` | h | Worked hours across the year |

**Vacation**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_vacation_remaining` | days | Days left, with entitlement, carry over and payout projection in attributes |
| `sensor.periodical_vacation_used` | days | Days taken |
| `sensor.periodical_vacation_total` | days | Entitlement plus any days saved from last year |

**Pay**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_pay_month_netto` | SEK | Net pay, with the full payroll breakdown in attributes |
| `sensor.periodical_pay_month_gross` | SEK | Gross pay |
| `sensor.periodical_pay_month_hours` | h | Hours booked by payroll |
| `sensor.periodical_pay_month_shifts` | shifts | Shifts booked by payroll |
| `sensor.periodical_pay_oncall_month` | SEK | On-call compensation |
| `sensor.periodical_pay_oncall_hours_month` | h | On-call hours |
| `sensor.periodical_pay_overtime_month` | SEK | Overtime pay |
| `sensor.periodical_pay_sick_days_month` | days | Sick days |
| `sensor.periodical_pay_sick_hours_month` | h | Sick hours |
| `sensor.periodical_pay_vab_days_month` | days | VAB days |
| `sensor.periodical_pay_leave_days_month` | days | Leave days |
| `sensor.periodical_ob_summary` | SEK | Total OB supplement, split per OB1 to OB5 in attributes |
| `sensor.periodical_sick_ob_summary` | SEK | OB paid during sickness, plus the amount forfeited |
| `sensor.periodical_absence_summary` | SEK | Absence deduction, with hours per absence type in attributes |

**Diagnostic**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_absences_count` | absences | Entries registered on `/absences` this year |
| `sensor.periodical_account` | | Display name from `/me`, with role and account flags in attributes |

## How schedule data is interpreted

The Periodical API returns raw rota data. A few rules turn that into something you can safely automate against.

### Absence beats the rota

The API keeps the rotation shift attached to a day even when you are not working it. A vacation day on a `N2` rotation still comes back with a 14:00 to 22:30 block. Every shift sensor checks the day status first, so on an absence day:

* `shift_start_today` and `shift_end_today` are blank
* `absent_today` is on, with the specific type in the `absence_type` attribute
* `working_today` is off
* the rota shift is still visible under `scheduled_shift_code`, `scheduled_shift_label`, `scheduled_start_time` and `scheduled_end_time`, so a card can still show what the day would have been

The same applies to tomorrow's sensors and to the weekly breakdown, which flags each day with `working` and `absence`.

### Overnight shifts

A night shift starting at 22:00 belongs to the day it began on. At 02:00 the following morning, `shift_start_today` still reports 22:00 yesterday and `shift_end_today` reports 06:30 today. The `shift_date` attribute tells you which calendar day the active shift is anchored to. Yesterday's shift is only treated as active while its end time is still in the future.

### On-call is not worked time

On-call is written as `00:00` to `00:00` with an overnight flag, which reads as a full 24 hours. Payroll counts it separately, so the integration does too:

* `hours_this_week` and `hours_this_year` cover worked hours only
* on-call appears in those sensors' attributes as `oncall_hours`, alongside `total_hours_including_oncall`
* `pay_oncall_month` and `pay_oncall_hours_month` carry the payroll figures

This keeps the hour sensors reconcilable against your payslip instead of running roughly 24 hours high per on-call day.

## Update strategy

A single 15 minute coordinator cycle refreshes each endpoint on its own schedule, so slow moving data does not get polled at the rate of fast moving data.

| Interval | Endpoints |
|---|---|
| 15 minutes | `/status`, the yesterday to tomorrow schedule window, `/next-shift` |
| 1 hour | `/schedule/week/{date}`, `/absences` |
| 4 hours | `/schedule/month`, `/vacation/balance` |
| 24 hours | `/me`, `/shifts`, `/schedule/year`, `/pay/month` |

Endpoints whose answer is scoped to the current day, month or year are force refreshed the moment the local calendar rolls over, regardless of when they were last fetched. Without that, `/pay/month` would keep serving last month's payslip for up to 24 hours after midnight on the first.

When an endpoint fails, its last good value is served and `api_problem` turns on with the failing endpoint listed in attributes. Repeated network failures open a circuit breaker for five minutes so a dead API is not hammered. Authentication failures are treated differently: they cannot recover on their own, so they go straight to the re-authentication flow.

### Endpoints used

```text
GET /me
GET /shifts
GET /users/{user_id}/status
GET /users/{user_id}/schedule?from_date={from}&to_date={to}
GET /users/{user_id}/schedule/week/{date}
GET /users/{user_id}/schedule/month
GET /users/{user_id}/schedule/year
GET /users/{user_id}/pay/month
GET /users/{user_id}/vacation/balance
GET /users/{user_id}/absences
GET /users/{user_id}/next-shift
GET /users/{user_id}/schedule/{date}
```

All requests carry `Authorization: Bearer <api-key>` and `Accept: application/json`.

## Services

Five services cover lookups that do not warrant a permanent entity. Each returns its payload as a response variable and also fires a `periodical_*` event, so automations written against the event bus keep working.

Every service accepts an optional `config_entry_id`. Omit it and the call runs against every configured account.

| Service | Purpose |
|---|---|
| `periodical.get_schedule_date` | Schedule for one date |
| `periodical.get_schedule_week` | Schedule for the ISO week containing a date |
| `periodical.get_schedule_range` | Schedule across a range, maximum 70 days |
| `periodical.get_pay_month` | Pay summary for a month |
| `periodical.get_vacation_balance` | Vacation balance for a year |

### Using the response

```yaml
actions:
  - action: periodical.get_schedule_range
    data:
      from_date: "2026-08-01"
      to_date: "2026-08-31"
    response_variable: august
  - action: notify.persistent_notification
    data:
      message: >
        August has
        {{ august.results.values() | map(attribute='days') | first
           | selectattr('status', 'eq', 'working') | list | count }}
        working days.
```

Results are keyed by Periodical user id, so a call that fans out across two accounts returns both. A failed lookup returns `{"error": "..."}` for that user instead of aborting the whole call.

### Using the event

```yaml
triggers:
  - trigger: event
    event_type: periodical_pay_month
conditions: []
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Net pay this month: {{ trigger.event.data.data.netto_pay }} SEK"
```

## Automation examples

### Wake up alarm on the morning of an early shift

```yaml
alias: Alarm before day shift
triggers:
  - trigger: template
    value_template: >
      {{ state_attr('sensor.periodical_tomorrow_shift_date', 'shift_code') == 'N1' }}
actions:
  - action: input_datetime.set_datetime
    target:
      entity_id: input_datetime.alarm
    data:
      time: "04:45:00"
```

### Do not disturb while working a night shift

```yaml
alias: Night shift quiet hours
triggers:
  - trigger: state
    entity_id: sensor.periodical_shift_start_today
conditions:
  - condition: state
    entity_id: binary_sensor.periodical_working_today
    state: "on"
  - condition: template
    value_template: >
      {{ state_attr('sensor.periodical_shift_start_today', 'shift_code') == 'N3' }}
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.do_not_disturb
```

### Nudge when vacation days are about to be lost

```yaml
alias: Vacation days expiring
triggers:
  - trigger: numeric_state
    entity_id: sensor.periodical_vacation_remaining
    above: 5
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        {{ states('sensor.periodical_vacation_remaining') }} vacation days left,
        {{ state_attr('sensor.periodical_vacation_remaining', 'projection').days_to_pay_out }}
        would be paid out instead of taken.
```

### Holiday mode while away

```yaml
alias: Holiday mode
triggers:
  - trigger: state
    entity_id: binary_sensor.periodical_absent_today
    to: "on"
conditions:
  - condition: template
    value_template: >
      {{ state_attr('binary_sensor.periodical_absent_today', 'absence_type') == 'vacation' }}
actions:
  - action: climate.set_preset_mode
    target:
      entity_id: climate.house
    data:
      preset_mode: away
```

## Troubleshooting

Turn on debug logging first. It reports every fetch, every skipped endpoint and every retry.

```yaml
logger:
  default: warning
  logs:
    custom_components.periodical: debug
```

### Setup fails with "invalid auth"

The key was rejected by `/me`. Confirm it is current in the Periodical portal and that you pasted the token only, without a `Bearer ` prefix.

### Setup fails with "cannot connect"

Home Assistant could not reach the host. Check the base URL and that your instance can resolve and reach it. Self hosted deployments usually need the full path including `/api/v1`.

### Everything is unavailable after working fine

Look at `binary_sensor.periodical_api_problem`. Its attributes name the failing endpoints, whether stale data is being served, and whether the circuit breaker has opened. If a repair notification is waiting, the key was revoked and needs replacing.

### A shift sensor is blank on a day I am working

Check `sensor.periodical_status_today`. Anything other than `working` blanks the shift sensors by design. The rota shift for that day is still in the `scheduled_*` attributes.

### Hour totals disagree with my payslip

Compare `hours_this_week` against its `oncall_hours` attribute. Worked and on-call hours are deliberately separate. `pay_month_hours` is payroll's own figure and is the authoritative one.

### Duplicate entities with a `_2` suffix

An older install left rows in the entity registry. Remove the stale entities from **Settings** > **Devices & services** > **Entities**, then reload the integration.

## Repository layout

```text
.
├── custom_components/
│   └── periodical/
│       ├── api.py            HTTP client: retries, backoff, circuit breaker
│       ├── coordinator.py    Tiered refresh and calendar rollover
│       ├── schedule.py       Reading rota payloads: status, shifts, hours
│       ├── entity.py         Shared entity identity and registry migration
│       ├── sensor.py         Sensor definitions
│       ├── binary_sensor.py  Binary sensor definitions
│       ├── config_flow.py    Setup and re-authentication
│       ├── services.py       Service handlers
│       ├── frontend/          Bundled Periodical Lovelace card
│       └── translations/
├── hacs.json
└── README.md
```

`schedule.py` is the single definition of what "working", "absent" and "on-call" mean. Both platforms read from it so they cannot drift apart.

## Credits and license

The upstream Periodical application is maintained separately by [KalleL94](https://github.com/KalleL94/Periodical). This repository only contains the Home Assistant integration.

Released under the MIT License. See [LICENSE](LICENSE).

<!-- Badge references -->
[hacs-shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[hacs-url]: https://github.com/hacs/integration
[release-shield]: https://img.shields.io/github/v/release/swetoast/ha-periodical?style=for-the-badge
[release-url]: https://github.com/swetoast/ha-periodical/releases
[license-shield]: https://img.shields.io/github/license/swetoast/ha-periodical?style=for-the-badge
[license-url]: https://github.com/swetoast/ha-periodical/blob/main/LICENSE
[downloads-shield]: https://img.shields.io/github/downloads/swetoast/ha-periodical/total?style=for-the-badge
[ha-shield]: https://img.shields.io/badge/Home%20Assistant-2024.11%2B-41BDF5.svg?style=for-the-badge
[ha-url]: https://www.home-assistant.io
