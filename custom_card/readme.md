

## Lovelace Custom Card

This repository also includes a Lovelace custom card for the Periodical integration.

Card file location in the repository:

```text
custom_card/periodical-card.js
```

The card auto-discovers Periodical entities and displays a compact overview of:

- current working status
- today's shift start and end time
- shift progress
- co-workers grouped by shift type
- OB supplement for today
- next upcoming shift
- tomorrow's shift
- weekly shift/hour summary
- monthly pay and hours
- yearly shift/hour summary
- vacation balance
- absence count

### Install the card

Copy the card JavaScript file from the repository:

```text
custom_card/periodical-card.js
```

to your Home Assistant `www` folder:

```text
config/www/periodical-card.js
```

If the `www` folder does not exist, create it first.

Restart Home Assistant or reload the browser cache after copying the file.

### Add Lovelace resource

Add the card as a dashboard resource:

```text
Settings → Dashboards → Resources → Add Resource
```

Use:

```text
URL: /local/periodical-card.js
Resource type: JavaScript module
```

YAML resource format:

```yaml
resources:
  - url: /local/periodical-card.js
    type: module
```

### Minimal card configuration

The card can auto-discover all Periodical entities, so the minimal configuration is:

```yaml
type: custom:periodical-card
```

### Optional title override

```yaml
type: custom:periodical-card
name: My Schedule
```

### Multiple Periodical users

If you have multiple Periodical users/entities and auto-discovery picks the wrong one, set the entity prefix manually.

Example: if your entities are named like this:

```text
sensor.user_shift_start_today
binary_sensor.user_working_today
```

Use:

```yaml
type: custom:periodical-card
name: User Schedule
user_prefix: user
```

### Manual entity overrides

The card supports manual entity overrides through the `entities` config object if needed.

Example:

```yaml
type: custom:periodical-card
name: My Schedule
entities:
  working_today: binary_sensor.user_working_today
  absent_today: binary_sensor.user_absent_today
  shift_start: sensor.user_shift_start_today
  shift_end: sensor.user_shift_end_today
  status_today: sensor.user_status_today
  coworkers_today: sensor.user_co_workers_today
  ob_today: sensor.user_ob_supplement_today
  rotation_week: sensor.user_rotation_week
  next_shift_date: sensor.user_next_shift_date
  next_shift_start: sensor.user_next_shift_start
  next_shift_end: sensor.user_next_shift_end
  tomorrow_date: sensor.user_tomorrow_shift_date
  tomorrow_start: sensor.user_tomorrow_shift_start
  tomorrow_end: sensor.user_tomorrow_shift_end
  shifts_week: sensor.user_shifts_this_week
  hours_week: sensor.user_hours_this_week
  working_days: sensor.user_working_days_this_month
  pay_gross: sensor.user_monthly_pay_gross
  pay_netto: sensor.user_monthly_pay_netto
  pay_hours: sensor.user_monthly_hours_worked
  pay_shifts: sensor.user_monthly_shifts
  pay_oncall: sensor.user_monthly_on_call_pay
  pay_oncall_hours: sensor.user_monthly_on_call_hours
  pay_overtime: sensor.user_monthly_overtime_pay
  pay_sick_days: sensor.user_monthly_sick_days
  pay_sick_hours: sensor.user_monthly_sick_hours
  pay_vab_days: sensor.user_monthly_vab_days
  pay_leave_days: sensor.user_monthly_leave_days
  shifts_year: sensor.user_shifts_this_year
  shifts_remaining: sensor.user_shifts_remaining_this_year
  hours_year: sensor.user_hours_this_year
  vacation_remaining: sensor.user_vacation_days_remaining
  vacation_total: sensor.user_vacation_days_total
  vacation_used: sensor.user_vacation_days_used
  absences: sensor.user_absences_this_year
```

### Card editor

The card includes a visual Lovelace card editor with fields for:

- card title
- user prefix

Manual entity mapping is usually not needed because the card discovers the integration entities automatically.

### Browser cache

If changes to `periodical-card.js` do not show up, clear the browser cache or add a temporary cache-busting query string to the resource URL:

```text
/local/periodical-card.js?v=3.0
```

Update the version number when replacing the card file.
