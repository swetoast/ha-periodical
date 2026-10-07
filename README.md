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
- [Lovelace card](#lovelace-card)
- [How schedule data is interpreted](#how-schedule-data-is-interpreted)
- [Update strategy](#update-strategy)
- [Services](#services)
- [Automation examples](#automation-examples)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Repository layout](#repository-layout)
- [Credits and license](#credits-and-license)

## Features

* UI config flow, no YAML required
* Automatic re-authentication when the API key is revoked or rotated
* 37 sensors and 5 binary sensors across schedule, absence, on-call, vacation and payroll
* Absence aware: vacation, sick, VAB and leave days report as absent instead of showing the rota shift
* Overnight aware: a night shift started at 22:00 yesterday is still the active shift at 02:00 today, with that shift's own co-workers
* On-call aware: stand-by is tracked separately from work, so shift and hour totals reconcile with your payslip
* Tiered polling that matches how fast each endpoint actually changes, with forced refresh at day, month and year boundaries
* Five services for ad hoc lookups, returning data both as a response variable and as an event
* Multi account capable: add a second Periodical user as a second config entry
* Bundled Lovelace card, registered automatically, no separate download
* English and Swedish translations

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

### Manual

1. Download the repository from [GitHub](https://github.com/swetoast/ha-periodical)
2. Copy the `periodical` folder into `config/custom_components/` on your Home Assistant instance
3. Restart Home Assistant

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
    ├── en.json
    └── sv.json
```

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=periodical)

Or navigate to **Settings** > **Devices & services** > **Add integration** and search for **Periodical**.

| Field | Required | Notes |
|---|---|---|
| Server address | Yes | The Periodical server you use, for example `https://periodical.example.com`. The `/api/v1` path is added for you if you leave it out |
| API Key | Yes | Your personal bearer token from the Periodical web portal, under Settings > API |

There is no default server. Periodical is self hosted, and a built in address would send every new user's API key to whoever runs it.

The key is validated immediately by calling `/me`. The numeric user id from that response identifies the account, so the entry survives a server address change without duplicating itself.

### Re-authentication

If the API key is revoked or rotated, the integration raises a repair notification rather than quietly serving stale data. Open it, enter the new key, and everything reloads in place. A key belonging to a different Periodical user is rejected, since accepting it would silently repoint every entity at somebody else's rota.

### Multiple accounts

Add the integration again with a second API key. Each account gets its own device and its own set of entities. The first configured account keeps the short `sensor.periodical_*` entity ids; later accounts are scoped by user id, for example `sensor.periodical_14_status_today`.

## Entities

All entities live under a single device named after the Periodical account.

### Binary sensors

| Entity | Description |
|---|---|
| `binary_sensor.periodical_working_today` | On when you are actually at work today, including the tail of last night's shift and call-ins from on-call. Off on pure on-call days |
| `binary_sensor.periodical_on_call_today` | On when today's rota shift is on-call stand-by. The `called_in` attribute shows whether overtime was booked |
| `binary_sensor.periodical_absent_today` | On for vacation, sick, VAB, leave and parental days, once any carried over night shift has ended |
| `binary_sensor.periodical_api_problem` | Diagnostic. On when an endpoint failed, data is stale, or the circuit breaker is open |
| `binary_sensor.periodical_account_active` | Diagnostic. Reflects the `is_active` flag from `/me` |

### Sensors

**Today**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_shift_start_today` | timestamp | Start of the shift currently in effect, blank on absence and days off |
| `sensor.periodical_shift_end_today` | timestamp | End of that shift, rolled past midnight when it runs overnight |
| `sensor.periodical_status_today` | | `working`, `off`, `vacation`, `sick`, `vab`, `leave`, `parental` or `unknown` |
| `sensor.periodical_coworkers_today` | people | Everyone on the day's roster. The `same_shift` attribute narrows it to the people on your own shift. During a carried over night shift the roster is the one from the day that shift started |
| `sensor.periodical_ob_today` | SEK | Inconvenient hours supplement earned today |
| `sensor.periodical_rotation_week` | | Position in the rotation cycle |

**Upcoming**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_tomorrow_shift_date` | date | Tomorrow's date, blank unless tomorrow is actually worked |
| `sensor.periodical_tomorrow_shift_start` | | Tomorrow's start time as `HH:MM` |
| `sensor.periodical_tomorrow_shift_end` | | Tomorrow's end time as `HH:MM` |
| `sensor.periodical_next_shift_date` | date | Next working day, skipping days off and booked absence. This can be an on-call day; check the `on_call` attribute |
| `sensor.periodical_next_shift_start` | | Its start time as `HH:MM` |
| `sensor.periodical_next_shift_end` | | Its end time as `HH:MM` |

**Aggregates**

| Entity | Unit | Description |
|---|---|---|
| `sensor.periodical_shifts_this_week` | shifts | Worked shifts in the current ISO week, on-call excluded, with a day by day breakdown in attributes |
| `sensor.periodical_hours_this_week` | h | Worked hours this week, with on-call and overtime reported separately in attributes |
| `sensor.periodical_working_days_month` | days | Worked shifts in the current calendar month, on-call excluded |
| `sensor.periodical_shifts_this_year` | shifts | Worked shifts across the year, on-call excluded |
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

## Lovelace card

The integration ships its own dashboard card. On startup it is served from `/periodical-static/periodical-card.js` and added to your Lovelace resources, so there is nothing to download or register by hand. The resource URL carries the integration version, so browsers pick up a new card after every update.

Add it to a dashboard with:

```yaml
type: custom:periodical-card
```

It finds your Periodical entities on its own and shows today's shift with a progress bar and time remaining, who you are working with grouped by shift, tomorrow and the next shift, and this week, month and year at a glance, along with pay, OB, vacation and absences. On-call days show as "On call" rather than as a working day or a day off.

| Option | Description |
|---|---|
| `name` | Title shown on the card. Defaults to the Periodical user name |
| `user_prefix` | Only needed with more than one Periodical account, for example `periodical_14` |

Both options are also available in the visual editor.

If your dashboards are in YAML mode, Home Assistant cannot add resources for you. Add this under `lovelace:` in `configuration.yaml` instead:

```yaml
lovelace:
  resources:
    - url: /periodical-static/periodical-card.js
      type: module
```

If you installed an earlier copy of the card under `/local/`, remove that resource. Both copies can load side by side without errors, but only one of them will be used.

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

A night shift starting at 22:00 belongs to the day it began on. At 02:00 the following morning:

* `shift_start_today` reports 22:00 yesterday and `shift_end_today` reports 06:30 today
* `working_today` is on, even if today itself is a day off
* `coworkers_today` uses the roster from the day the shift started, and `same_shift` names who is on nights with you
* `absent_today` stays off on the first morning of a holiday until the shift ends

The API reports a running overnight shift in `currently_active_shift` on `/status`, and that field takes precedence over the day's own fields. If it is missing, the integration falls back to yesterday's entry in the schedule window. Either way, the shift is only treated as active while its end time is still in the future, and the `carried_over` and `shift_date` attributes show which day it is anchored to.

`status_today` always reports the day's own status, so a day off that starts with the end of a night shift still reads `off`.

### On-call is not worked time

On-call is written as `00:00` to `00:00` with an overnight flag and a status of `working`, which reads as a full 24 hour shift. Payroll books it as neither a shift nor worked hours, so the integration follows payroll:

* shift counters and `working_days_month` exclude on-call days
* `hours_this_week` and `hours_this_year` cover worked shift hours only
* the attributes on those sensors carry `oncall_days`, `oncall_hours`, `overtime_hours` and `total_hours_including_oncall`
* `on_call_today` is on for stand-by days, while `working_today` stays off unless you were called in

Being called in from on-call is booked as overtime on the day. It turns `working_today` on and shows up as `overtime_hours`, but does not add a shift, again matching payroll.

Checked against real data: the June 2026 schedule has 22 days with status `working`, of which 4 are on-call. The integration reports 18 shifts, 153 worked hours and 96 on-call hours, which is exactly what that month's payslip shows.

## Update strategy

A single 15 minute coordinator cycle refreshes each endpoint on its own schedule, so slow moving data does not get polled at the rate of fast moving data.

| Interval | Endpoints |
|---|---|
| 15 minutes | `/status`, the yesterday to tomorrow schedule window, `/next-shift` |
| 1 hour | `/schedule/week/{date}`, `/absences` |
| 4 hours | `/schedule/month`, `/vacation/balance` |
| 24 hours | `/me`, `/shifts`, `/schedule/year`, `/pay/month` |

Endpoints whose answer is scoped to the current day, month or year are force refreshed the moment the local calendar rolls over, regardless of when they were last fetched. An extra refresh is scheduled just after midnight, so day based sensors switch over at 00:00 instead of up to one polling interval later. Without that, `/pay/month` would keep serving last month's payslip for up to 24 hours after midnight on the first.

When an endpoint fails, its last good value is served and `api_problem` turns on with the failing endpoint listed in attributes. Repeated network failures open a circuit breaker for five minutes so a dead API is not hammered. A rejected key (HTTP 401) cannot recover on its own, so it goes straight to the re-authentication flow. HTTP 403 is treated as a per endpoint permission problem instead, because pay, vacation and absences are restricted to your own user or an admin. It only triggers re-authentication when it comes from `/me`, which any valid key can read.

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

Home Assistant could not reach the server. Check the address and that your Home Assistant instance can resolve and reach it. If your server serves the API somewhere other than `/api/v1`, enter the full path.

### Everything is unavailable after working fine

Look at `binary_sensor.periodical_api_problem`. Its attributes name the failing endpoints, whether stale data is being served, and whether the circuit breaker has opened. If a repair notification is waiting, the key was revoked and needs replacing.

### A shift sensor is blank on a day I am working

Check `sensor.periodical_status_today`. Anything other than `working` blanks the shift sensors by design. The rota shift for that day is still in the `scheduled_*` attributes.

### Hour totals disagree with my payslip

Worked, on-call and overtime hours are deliberately separate, and the worked figures are built to match the payslip's `total_hours` and `num_shifts`. If they still differ, compare against `pay_month_hours`, which is payroll's own figure and the authoritative one. A late change to the rota can make the schedule and a closed payslip disagree.

### Working Today is off on an on-call day

That is intended. On-call is stand-by, so it has its own `binary_sensor.periodical_on_call_today`. If you were called in, the overtime booked on the day turns `working_today` on.

### Duplicate entities with a `_2` suffix

An older install left rows in the entity registry. Remove the stale entities from **Settings** > **Devices & services** > **Entities**, then reload the integration.

## Development

The tests run against a real Home Assistant through `pytest-homeassistant-custom-component`:

```bash
pip install -r requirements_test.txt
pytest
```

They cover the config flow, setting up and unloading the whole integration, the API client contract, the schedule rules (absence, overnight shifts, on-call, overtime), card registration, and that the Swedish translation has every key the English one has.

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
│       ├── frontend/         Bundled Lovelace card and its registration
│       └── translations/     English and Swedish
├── tests/
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
[license-shield]: https://img.shields.io/github/license/swetoast/ha-periodical?style=for-the-badge
[license-url]: https://github.com/swetoast/ha-periodical/blob/main/LICENSE
[ha-shield]: https://img.shields.io/badge/Home%20Assistant-2024.11%2B-41BDF5.svg?style=for-the-badge
[ha-url]: https://www.home-assistant.io
