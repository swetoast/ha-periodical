/**
 * periodical-card.js v6.9
 * Custom Lovelace card for the Periodical Home Assistant integration.
 *
 * Install : copy to <config>/www/periodical-card.js
 * Resource: /local/periodical-card.js?v=6.9  (type: module)
 *
 * Minimal config:
 *   type: custom:periodical-card
 */

const CARD_VERSION = '6.9';

console.info(`%c periodical-card ${CARD_VERSION} `, 'background:#0ea5e9;color:#fff;border-radius:3px');

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function safeColor(value) {
  if (!value) return null;
  const v = String(value).trim();
  return /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(v) ? v : null;
}

function discoverPrefix(hass, forced) {
  const exists = (prefix) =>
    !!hass.states[`binary_sensor.${prefix}_working_today`] ||
    !!hass.states[`sensor.${prefix}_status_today`] ||
    !!hass.states[`sensor.${prefix}_shift_start_today`];

  if (forced && exists(forced)) return forced;
  if (exists('periodical')) return 'periodical';

  for (const eid of Object.keys(hass.states)) {
    const m = eid.match(/^(?:sensor|binary_sensor)\.(periodical[a-z0-9_]*)_(?:shift_start_today|working_today|status_today)$/);
    if (m && exists(m[1])) return m[1];
  }

  return null;
}

const ENTITY_MAP = {
  working_today: { domain: 'binary_sensor', suffix: 'working_today' },
  absent_today: { domain: 'binary_sensor', suffix: 'absent_today' },
  shift_start: { domain: 'sensor', suffix: 'shift_start_today' },
  shift_end: { domain: 'sensor', suffix: 'shift_end_today' },
  status_today: { domain: 'sensor', suffix: 'status_today' },
  coworkers_today: { domain: 'sensor', suffix: 'coworkers_today' },
  ob_today: { domain: 'sensor', suffix: 'ob_today' },
  rotation_week: { domain: 'sensor', suffix: 'rotation_week' },
  next_shift_date: { domain: 'sensor', suffix: 'next_shift_date' },
  next_shift_start: { domain: 'sensor', suffix: 'next_shift_start' },
  next_shift_end: { domain: 'sensor', suffix: 'next_shift_end' },
  tomorrow_date: { domain: 'sensor', suffix: 'tomorrow_shift_date' },
  tomorrow_start: { domain: 'sensor', suffix: 'tomorrow_shift_start' },
  tomorrow_end: { domain: 'sensor', suffix: 'tomorrow_shift_end' },
  shifts_week: { domain: 'sensor', suffix: 'shifts_this_week' },
  hours_week: { domain: 'sensor', suffix: 'hours_this_week' },
  working_days: { domain: 'sensor', suffix: 'working_days_month' },
    pay_gross: { domain: 'sensor', suffix: 'pay_month_gross' },
  pay_netto: { domain: 'sensor', suffix: 'pay_month_netto' },
  pay_hours: { domain: 'sensor', suffix: 'pay_month_hours' },
  pay_shifts: { domain: 'sensor', suffix: 'pay_month_shifts' },
  pay_oncall: { domain: 'sensor', suffix: 'pay_oncall_month' },
  pay_oncall_hours: { domain: 'sensor', suffix: 'pay_oncall_hours_month' },
  pay_overtime: { domain: 'sensor', suffix: 'pay_overtime_month' },
  pay_sick_days: { domain: 'sensor', suffix: 'pay_sick_days_month' },
  pay_sick_hours: { domain: 'sensor', suffix: 'pay_sick_hours_month' },
  pay_vab_days: { domain: 'sensor', suffix: 'pay_vab_days_month' },
  pay_leave_days: { domain: 'sensor', suffix: 'pay_leave_days_month' },
  shifts_year: { domain: 'sensor', suffix: 'shifts_this_year' },
  shifts_remaining: { domain: 'sensor', suffix: 'shifts_remaining_year' },
  hours_year: { domain: 'sensor', suffix: 'hours_this_year' },
    vacation_remaining: { domain: 'sensor', suffix: 'vacation_remaining' },
  vacation_total: { domain: 'sensor', suffix: 'vacation_total' },
  vacation_used: { domain: 'sensor', suffix: 'vacation_used' },
  absences: { domain: 'sensor', suffix: 'absences_count' },
  ob_summary: { domain: 'sensor', suffix: 'ob_summary' },
  sick_ob_summary: { domain: 'sensor', suffix: 'sick_ob_summary' },
  absence_summary: { domain: 'sensor', suffix: 'absence_summary' },
  api_problem: { domain: 'binary_sensor', suffix: 'api_problem' },
  };

function parseLocalDate(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return null;
  const m = String(str).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}

function parseTime(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return null;
  const raw = String(str);
  const hm = raw.match(/^(\d{1,2}):(\d{2})$/);

  if (hm) {
    const d = new Date();
    d.setHours(+hm[1], +hm[2], 0, 0);
    return d;
  }

  const iso = new Date(raw);
  return isNaN(iso) ? null : iso;
}

function formatTime(str) {
  const d = parseTime(str);
  return d ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '--:--';
}

function formatDate(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return '—';
  const d = parseLocalDate(str) || new Date(str);
  return isNaN(d) ? String(str) : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

function daysUntil(str) {
  const target = parseLocalDate(str);
  if (!target) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target - today) / 86400000);
}

function toLocalMins(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return null;
  const m = String(str).match(/^(\d{1,2}):(\d{2})/);
  if (!m) return null;
  return +m[1] * 60 + +m[2];
}

function shiftRemaining(startStr, endStr, overnightFlag = null) {
  const now = new Date();
  const nowMins = now.getHours() * 60 + now.getMinutes();
  const startMins = toLocalMins(startStr);
  const endMins = toLocalMins(endStr);

  if (startMins === null || endMins === null) return null;

  const overnight = (overnightFlag === true || overnightFlag === false)
    ? overnightFlag
    : endMins <= startMins;

  if (!overnight) {
    if (nowMins < startMins || nowMins >= endMins) return null;
    return endMins - nowMins;
  }

  if (nowMins >= startMins) return (1440 - nowMins) + endMins;
  if (nowMins < endMins) return endMins - nowMins;

  return null;
}

function formatDuration(mins) {
  if (mins === null || mins < 0) return null;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function shiftProgress(startStr, endStr, overnightFlag = null) {
  const now = new Date();
  const nowMins = now.getHours() * 60 + now.getMinutes();
  const startMins = toLocalMins(startStr);
  const endMins = toLocalMins(endStr);

  if (startMins === null || endMins === null) return null;

  const overnight = (overnightFlag === true || overnightFlag === false)
    ? overnightFlag
    : endMins <= startMins;

  let elapsed;
  let duration;

  if (!overnight) {
    if (nowMins < startMins) return 0;
    if (nowMins >= endMins) return 100;
    elapsed = nowMins - startMins;
    duration = endMins - startMins;
  } else {
    duration = (1440 - startMins) + endMins;

    if (nowMins >= startMins) {
      elapsed = nowMins - startMins;
    } else if (nowMins < endMins) {
      elapsed = (1440 - startMins) + nowMins;
    } else {
      return 0;
    }
  }

  return Math.max(0, Math.min(100, Math.round((elapsed / duration) * 100)));
}

function fmtSEK(val) {
  const n = Number(val);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('sv-SE', {
    style: 'currency',
    currency: 'SEK',
    maximumFractionDigits: 0,
  });
}

function fmtNum(val, maxDigits = 1) {
  const n = Number(val);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('sv-SE', { maximumFractionDigits: maxDigits });
}

const SHIFT_LABELS_EN = {
  N1: 'Day',
  N2: 'Evening',
  N3: 'Night',
  OC: 'On-call',
  OT: 'Overtime',
  'OT-N1': 'OT day',
  'OT-N2': 'OT evening',
  'OT-N3': 'OT night',
  OFF: 'Off',
  SEM: 'Vacation',
  SICK: 'Sick',
  VAB: 'VAB',
  LEAVE: 'Leave',
};

const SWEDISH_EN = [
  [/natt/i, 'Night'],
  [/kv[\u00e4a]ll/i, 'Evening'],
  [/dag/i, 'Day'],
  [/beredskap|jour/i, 'On-call'],
  [/\u00f6vertid/i, 'Overtime'],
  [/semester/i, 'Vacation'],
  [/sjuk/i, 'Sick'],
  [/ledig/i, 'Off'],
];

function englishLabel(code, label) {
  const c = String(code || '').toUpperCase();
  if (SHIFT_LABELS_EN[c]) return SHIFT_LABELS_EN[c];

  const l = String(label || '');
  for (const [re, en] of SWEDISH_EN) {
    if (re.test(l)) return en;
  }

  return code || label || '';
}

const SHIFT_GROUPS = [
  { key: 'day', label: 'Day shift', match: (c, l) => /dag/i.test(l) || /^(D|N1)\b/i.test(c) },
  { key: 'evening', label: 'Evening shift', match: (c, l) => /kv[äa]ll/i.test(l) || /^(E|K|N2)\b/i.test(c) },
  { key: 'night', label: 'Night shift', match: (c, l) => /natt/i.test(l) || /^(N|N3)\b/i.test(c) },
  { key: 'oncall', label: 'On call', match: (c, l) => /^OC$/i.test(c) || /beredskap|on.?call|jour/i.test(l) },
  { key: 'overtime', label: 'Overtime', match: (c, l) => /^OT$/i.test(c) || /övertid|overtime|extra/i.test(l) },
];

const OB_COLORS = {
  OB1: '#0ea5e9',
  OB2: '#38bdf8',
  OB3: '#22d3ee',
  OB4: '#14b8a6',
  OB5: '#10b981',
};

function classifyShift(code, label) {
  for (const g of SHIFT_GROUPS) {
    if (g.match(code || '', label || '')) return g;
  }

  return { key: 'other', label: label || code || 'Other' };
}

function groupCoworkers(members) {
  const map = {};

  for (const cw of members) {
    const g = classifyShift(cw.shift_code, cw.shift_label);
    if (!map[g.key]) map[g.key] = { ...g, members: [] };
    map[g.key].members.push(cw);
  }

  return ['day', 'evening', 'night', 'oncall', 'overtime', 'other']
    .map((k) => map[k])
    .filter(Boolean);
}

function cwName(cw) {
  return typeof cw === 'string' ? cw : (cw?.name || '?');
}

const CARD_CSS = `
  :host {
    --metro-bg-dark: #1a2332;
    --metro-bg-card: #243447;
    --metro-bg-elevated: #2d3f56;
    --metro-text-primary: #ffffff;
    --metro-text-secondary: #cbd5e1;
    --metro-text-tertiary: #94a3b8;
    --metro-divider: rgba(255, 255, 255, 0.06);
    --metro-accent: #0ea5e9;
    --metro-success: #10b981;
    --metro-warning: #f59e0b;
    --metro-error: #ef4444;
    --md-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
    --r: 14px;
    --rs: 8px;
    --gap: 12px;
    display: block;
  }

  * { box-sizing: border-box; }

  .card {
    background: var(--metro-bg-dark);
    border-radius: var(--r);
    overflow: hidden;
    color: var(--metro-text-primary);
    font-family: 'Segoe UI','San Francisco',-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
    font-weight: 300;
    box-shadow: 0 8px 30px rgba(0,0,0,.25);
  }

  .hero {
    padding: 20px 20px 18px;
    border-bottom: 1px solid var(--metro-divider);
  }

  .hero-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
    gap: 12px;
  }

  .hero-id {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }

  .h-icon {
    width: 42px;
    height: 42px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
  }

  .h-icon ha-icon { --mdc-icon-size: 22px; }

  .icon-working {
    background: color-mix(in srgb,var(--metro-success) 16%,transparent);
    color: var(--metro-success);
  }

  .icon-off {
    background: var(--metro-bg-card);
    color: var(--metro-text-tertiary);
  }

  .icon-absent {
    background: color-mix(in srgb,var(--metro-error) 16%,transparent);
    color: var(--metro-error);
  }

  .hero-name {
    font-size: 20px;
    font-weight: 300;
    letter-spacing: .3px;
    line-height: 1.1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .hero-state {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-top: 3px;
  }

  .state-working { color: var(--metro-success); }
  .state-off { color: var(--metro-text-tertiary); }
  .state-absent { color: var(--metro-error); }

  .hero-badge {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .5px;
    padding: 6px 12px;
    border-radius: 20px;
    white-space: nowrap;
    flex: 0 0 auto;
  }

  .hero-times {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 14px;
    gap: 12px;
  }

  .ht-big {
    font-size: 34px;
    font-weight: 200;
    line-height: 1;
  }

  .ht-lbl {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
    margin-top: 6px;
  }

  .ht-lbl.r { text-align: right; }
  .ht-mid { text-align: center; }

  .ht-rem {
    font-size: 18px;
    font-weight: 300;
    line-height: 1;
    color: var(--metro-text-secondary);
  }

  .ht-rem-lbl {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
    margin-top: 6px;
  }

  .ht-arrow {
    font-size: 13px;
    color: var(--metro-text-tertiary);
    padding-bottom: 14px;
  }

  .prog-track {
    height: 6px;
    border-radius: 3px;
    background: var(--metro-bg-card);
    overflow: hidden;
    margin-bottom: 8px;
  }

  .prog-fill {
    height: 100%;
    border-radius: 3px;
    background: var(--metro-accent);
    transition: width .3s ease;
  }

  .pnone .prog-fill { opacity: .35; }

  .prog-foot {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }

  .pf-txt {
    font-size: 11px;
    font-weight: 300;
    color: var(--metro-text-secondary);
  }

  .pf-pct {
    font-size: 13px;
    font-weight: 400;
    color: var(--metro-accent);
  }

  .hero-off-msg {
    text-align: center;
    padding: 8px 0 2px;
    color: var(--metro-text-secondary);
    font-size: 14px;
    font-weight: 300;
  }

  .body { padding: 16px 20px 4px; }

  .sec-lbl {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--metro-text-tertiary);
    margin: 4px 0 10px;
  }

  .tile {
    background: var(--metro-bg-card);
    border-radius: var(--rs);
    box-shadow: var(--md-shadow);
  }

  .tile.pad { padding: 14px 16px; }
  .mb { margin-bottom: var(--gap); }

  .cw-group { margin-bottom: 10px; }
  .cw-group:last-child { margin-bottom: 0; }

  .cw-div {
    height: 1px;
    background: var(--metro-divider);
    margin: 10px 0;
  }

  .cw-lbl {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
    margin-bottom: 4px;
  }

  .cw-names {
    font-size: 13px;
    font-weight: 300;
    line-height: 1.5;
    color: var(--metro-text-secondary);
  }

  .cw-you {
    color: var(--metro-text-primary);
    font-weight: 600;
  }

  .stale-banner {
    background: color-mix(in srgb,var(--metro-warning) 14%,var(--metro-bg-card));
    border-left: 3px solid var(--metro-warning);
    border-radius: 0 var(--rs) var(--rs) 0;
    padding: 12px 14px;
    font-size: 12px;
    font-weight: 400;
    color: var(--metro-warning);
    box-shadow: var(--md-shadow);
  }

  .upcoming {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--gap);
  }

  .upcoming.solo { grid-template-columns: 1fr; }

  .nxt {
    border-radius: var(--rs);
    padding: 14px;
    box-shadow: var(--md-shadow);
    min-width: 0;
  }

  .nxt.now { background: var(--metro-bg-elevated); }
  .nxt.then { background: var(--metro-bg-card); }

  .nxt-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 8px;
  }

  .nxt-lbl {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
  }

  .nxt-when {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    padding: 3px 8px;
    border-radius: 20px;
    background: color-mix(in srgb,var(--metro-accent) 18%,transparent);
    color: var(--metro-accent);
    white-space: nowrap;
  }

  .nxt-times {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  .nxt-t {
    font-size: 20px;
    font-weight: 300;
  }

  .nxt-t.dim { color: var(--metro-text-secondary); }

  .nxt-arr {
    font-size: 12px;
    color: var(--metro-text-tertiary);
  }

  .nxt-date {
    font-size: 11px;
    font-weight: 300;
    color: var(--metro-text-secondary);
    margin-top: 4px;
  }

  .stat-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--gap);
  }

  .stat {
    background: var(--metro-bg-card);
    border-radius: var(--rs);
    padding: 14px 16px;
    box-shadow: var(--md-shadow);
    min-width: 0;
  }

  .stat-num {
    font-size: 28px;
    font-weight: 200;
    line-height: 1;
  }

  .stat-unit {
    font-size: 12px;
    font-weight: 300;
    color: var(--metro-text-tertiary);
    margin-left: 3px;
  }

  .stat-lbl {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
    margin: 8px 0;
  }

  .stat-bar {
    height: 4px;
    border-radius: 2px;
    background: var(--metro-bg-dark);
    overflow: hidden;
  }

  .stat-bar-fill {
    height: 100%;
    border-radius: 2px;
    transition: width .3s ease;
  }

  .bar-accent { background: var(--metro-accent); }
  .bar-warning { background: var(--metro-warning); }
  .bar-success { background: var(--metro-success); }

  .stat-note {
    font-size: 11px;
    font-weight: 300;
    color: var(--metro-success);
    margin-top: 8px;
  }

  .pay-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  .pay-big {
    font-size: 22px;
    font-weight: 200;
    line-height: 1;
  }

  .pay-sub {
    font-size: 9px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
    margin-top: 6px;
  }

  .pay-div {
    height: 1px;
    background: var(--metro-divider);
    margin: 16px 0;
  }

  .pay-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .tag {
    font-size: 11px;
    font-weight: 400;
    padding: 5px 10px;
    border-radius: 20px;
    background: color-mix(in srgb,var(--metro-accent) 16%,transparent);
    color: var(--metro-accent);
  }

  .tag.warn {
    background: color-mix(in srgb,var(--metro-warning) 16%,transparent);
    color: var(--metro-warning);
  }

  .tag.muted {
    background: var(--metro-bg-dark);
    color: var(--metro-text-secondary);
  }

  .ob-panel {
    display: block;
  }

  .ob-summary {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 6px 12px;
    align-items: baseline;
    padding-bottom: 10px;
    margin-bottom: 10px;
    border-bottom: 1px solid var(--metro-divider);
  }

  .ob-title {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
  }

  .ob-total {
    font-size: 18px;
    font-weight: 300;
    color: var(--metro-text-primary);
    text-align: right;
    white-space: nowrap;
  }

  .ob-sub {
    grid-column: 1 / -1;
    font-size: 11px;
    font-weight: 300;
    color: var(--metro-text-secondary);
  }

  .ob-breakdown-wrap {
    display: grid;
    grid-template-columns: 104px 1fr;
    gap: 16px;
    align-items: center;
  }

  .ob-breakdown {
    display: flex;
    flex-direction: column;
    gap: 7px;
    min-width: 0;
  }

  .obx {
    display: grid;
    grid-template-columns: auto auto 1fr auto;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }

  .ob-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }

  .obx-code {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
  }

  .obx-pay {
    font-size: 13px;
    font-weight: 400;
    color: var(--metro-text-primary);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .obx-meta {
    font-size: 11px;
    font-weight: 300;
    color: var(--metro-text-tertiary);
    white-space: nowrap;
  }

  .ob-donut {
    width: 92px;
    height: 92px;
    border-radius: 50%;
    position: relative;
    justify-self: center;
    box-shadow: inset 0 0 0 1px var(--metro-divider);
  }

  .ob-donut::after {
    content: '';
    position: absolute;
    inset: 19px;
    background: var(--metro-bg-card);
    border-radius: 50%;
    box-shadow: 0 0 0 1px var(--metro-divider);
  }

  .ob-donut-center {
    position: absolute;
    inset: 0;
    z-index: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    pointer-events: none;
  }

  .ob-donut-title {
    font-size: 10px;
    font-weight: 600;
    color: var(--metro-text-tertiary);
    letter-spacing: .5px;
  }

  .ob-donut-value {
    font-size: 11px;
    font-weight: 400;
    color: var(--metro-text-secondary);
    margin-top: 2px;
  }

  .vac-row {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .vac-num {
    font-size: 28px;
    font-weight: 200;
    line-height: 1;
    white-space: nowrap;
  }

  .vac-unit {
    font-size: 11px;
    font-weight: 300;
    color: var(--metro-text-tertiary);
    margin-left: 4px;
  }

  .vac-right {
    flex: 1;
    min-width: 0;
  }

  .vac-sub {
    font-size: 11px;
    font-weight: 300;
    color: var(--metro-text-secondary);
    margin-bottom: 8px;
  }

  .footer-bar {
    display: flex;
    justify-content: center;
    gap: 24px;
    padding: 14px 20px;
    background: var(--metro-bg-card);
    border-top: 1px solid var(--metro-divider);
    flex-wrap: wrap;
  }

  .footer-item {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--metro-text-tertiary);
  }

  .not-found {
    text-align: center;
    padding: 28px 16px;
    color: var(--metro-text-secondary);
    font-size: 13px;
    font-weight: 300;
    line-height: 1.8;
  }

  @media (max-width: 380px) {
    .hero-times { gap: 8px; }
    .ht-big { font-size: 29px; }

    .upcoming,
    .stat-grid {
      grid-template-columns: 1fr;
    }

    .pay-grid {
      grid-template-columns: 1fr;
      gap: 14px;
    }

    .ob-breakdown-wrap {
      grid-template-columns: 92px 1fr;
      gap: 12px;
    }

    .obx {
      gap: 6px;
    }
  }
`;

class PeriodicalCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._timer = null;
  }

  static getStubConfig() {
    return {};
  }

  setConfig(c) {
    this._config = c || {};
    this._render();
  }

  set hass(h) {
    const old = this._hass;
    this._hass = h;

    if (this._shouldRender(old, h)) {
      this._render();
    }
  }

  getCardSize() {
    return 7;
  }

  connectedCallback() {
    clearInterval(this._timer);
    this._timer = setInterval(() => this._render(), 60_000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  _prefix() {
    return this._hass ? discoverPrefix(this._hass, this._config.user_prefix ?? null) : null;
  }

  _eid(k) {
    const ov = this._config?.entities?.[k];
    if (ov) return ov;

    const p = this._prefix();
    if (!p) return null;

    const e = ENTITY_MAP[k];
    return e ? `${e.domain}.${p}_${e.suffix}` : null;
  }

  _state(k) {
    const eid = this._eid(k);
    return (eid && this._hass) ? (this._hass.states[eid] ?? null) : null;
  }

  _val(k) {
    const s = this._state(k);
    if (!s) return null;
    return (s.state === 'unknown' || s.state === 'unavailable') ? null : s.state;
  }

  _num(k) {
    const v = parseFloat(this._val(k));
    return isNaN(v) ? null : v;
  }

  _attr(k, a) {
    return this._state(k)?.attributes?.[a] ?? null;
  }


  _relevantEids() {
    if (!this._prefix()) return [];

    const ids = [];
    for (const k of Object.keys(ENTITY_MAP)) {
      const e = this._eid(k);
      if (e) ids.push(e);
    }

    return ids;
  }

  _shouldRender(prev, next) {
    if (!prev || !next) return true;

    const ids = this._relevantEids();
    if (ids.length === 0) return true;

    return ids.some((id) => prev.states[id] !== next.states[id]);
  }

  _userName() {
    if (this._config.name) return this._config.name;

    const h = this._hass;

    try {
      const eid = this._eid('working_today');
      const ent = eid ? h?.entities?.[eid] : null;
      const dev = ent?.device_id ? h?.devices?.[ent.device_id] : null;
      const dn = dev?.name_by_user || dev?.name;

      if (dn) return dn;
    } catch (e) {
      // Registry data is not available in every Home Assistant frontend context.
    }

    const p = this._prefix();
    return p ? p.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : 'Periodical';
  }

  _render() {
    const shadow = this.shadowRoot;

    if (!this._hass) {
      shadow.innerHTML = '';
      return;
    }

    const prefix = this._prefix();

    if (!prefix) {
      shadow.innerHTML = `
        <style>${CARD_CSS}</style>
        <div class="card">
          <div class="not-found">
            No Periodical entities found.<br>
            Make sure the integration is installed.
          </div>
        </div>
      `;
      return;
    }

    const statusToday = String(this._val('status_today') || '').toLowerCase();

    const isWorking =
      this._val('working_today') === 'on' ||
      statusToday === 'working';

    const isAbsent =
      this._val('absent_today') === 'on' ||
      ['absent', 'sick', 'vab', 'leave', 'vacation'].includes(statusToday);

    const title = this._userName();
    const rotWeek = this._val('rotation_week');

    const shiftStart = this._val('shift_start');
    const shiftEnd = this._val('shift_end');
    const shiftLabel = this._attr('shift_start', 'shift_label') || this._attr('shift_end', 'shift_label');
    const shiftCode = this._attr('shift_start', 'shift_code') || this._attr('shift_end', 'shift_code');
    const shiftColor = safeColor(this._attr('shift_start', 'shift_color') || this._attr('shift_end', 'shift_color'));

    const progressStart = this._attr('shift_start', 'start_time') || shiftStart;
    const progressEnd = this._attr('shift_start', 'end_time') || shiftEnd;
    const shiftOvernight = this._attr('shift_start', 'overnight');

    const pct = isWorking ? shiftProgress(progressStart, progressEnd, shiftOvernight) : null;
    const remainMins = isWorking ? shiftRemaining(progressStart, progressEnd, shiftOvernight) : null;
    const remainTxt = formatDuration(remainMins);

    const coworkersRaw = this._attr('coworkers_today', 'co_workers') ?? [];
    const coworkers = Array.isArray(coworkersRaw) ? coworkersRaw : [];

    const selfEntry = (isWorking && shiftCode)
      ? {
          name: title,
          shift_code: shiftCode,
          shift_label: shiftLabel || shiftCode,
          isSelf: true,
        }
      : null;

    const cwGroups = groupCoworkers(selfEntry ? [selfEntry, ...coworkers] : coworkers);

    const nextDate = this._val('next_shift_date');
    const nextStart = this._val('next_shift_start') || this._attr('next_shift_date', 'start_time');
    const nextEnd = this._val('next_shift_end') || this._attr('next_shift_date', 'end_time');
    const nextCode = this._attr('next_shift_date', 'shift_code');
    const nextLabel = this._attr('next_shift_date', 'shift_label');
    const daysAway = daysUntil(nextDate);

    const tomDate = this._val('tomorrow_date');
    const tomStart = this._val('tomorrow_start') || this._attr('tomorrow_date', 'start_time');
    const tomEnd = this._val('tomorrow_end') || this._attr('tomorrow_date', 'end_time');
    const tomCode = this._attr('tomorrow_date', 'shift_code');
    const tomLabel = this._attr('tomorrow_date', 'shift_label');

    const shiftsWeek = this._num('shifts_week');
    const hoursWeek = this._num('hours_week');

    const payGross = this._num('pay_gross');
    const payNetto = this._num('pay_netto');
    const payHours = this._num('pay_hours');
    const payShifts = this._num('pay_shifts');
    const workingDays = this._num('working_days');
    const payOncall = this._num('pay_oncall');
    const payOncallHours = this._num('pay_oncall_hours');
    const payOvertime = this._num('pay_overtime');
    const paySickDays = this._num('pay_sick_days');
    const paySickHours = this._num('pay_sick_hours');
    const payVabDays = this._num('pay_vab_days');
    const payLeaveDays = this._num('pay_leave_days');

    const obToday = this._num('ob_today');
    const obMonthTotal = this._num('ob_summary');
    const obAttrs = this._state('ob_summary')?.attributes || {};
    const obMonthHours = Number(obAttrs.total_hours || 0);

    const obCodes = ['ob1', 'ob2', 'ob3', 'ob4', 'ob5']
      .map((c) => ({
        code: c.toUpperCase(),
        pay: Number(obAttrs[`${c}_pay`] || 0),
        hours: Number(obAttrs[`${c}_hours`] || 0),
        color: OB_COLORS[c.toUpperCase()] || '#0ea5e9',
      }))
      .filter((o) => o.pay > 0 || o.hours > 0);

    const sickObTotal = this._num('sick_ob_summary');
    const absMonthDed = this._num('absence_summary');

    const shiftsYear = this._num('shifts_year');
    const shiftsRemaining = this._num('shifts_remaining');
    const hoursYear = this._num('hours_year');

    const yearPct = (shiftsYear && shiftsRemaining !== null)
      ? Math.max(0, Math.min(100, Math.round(((shiftsYear - shiftsRemaining) / shiftsYear) * 100)))
      : null;

    const vacRem = this._num('vacation_remaining');
    const vacTotal = this._num('vacation_total');
    const vacUsed = this._num('vacation_used');

    const vacPct = (vacTotal > 0)
      ? Math.max(0, Math.min(100, Math.round(((vacUsed ?? 0) / vacTotal) * 100)))
      : null;

    const absences = this._num('absences');

    const vacProj = this._attr('vacation_remaining', 'projection') || {};
    const vacPayout = (typeof vacProj.payout_total === 'number') ? vacProj.payout_total : null;
    const vacDaysToSave = (typeof vacProj.days_to_save === 'number') ? vacProj.days_to_save : null;

    const apiKey = 'api_problem';

    const apiState = this._val(apiKey);
    const apiStale =
      apiState === 'on' ||
      this._attr(apiKey, 'using_stale_data') === true;

    let iconName;
    let iconClass;
    let stateClass;
    let stateText;
    let offMessage;

    if (isAbsent) {
      iconName = 'mdi:account-off';
      iconClass = 'icon-absent';
      stateClass = 'state-absent';
      stateText = 'Absent';
      offMessage = 'Absent today';
    } else if (isWorking) {
      iconName = 'mdi:briefcase-check';
      iconClass = 'icon-working';
      stateClass = 'state-working';
      stateText = 'Working';
      offMessage = '';
    } else {
      iconName = 'mdi:home-outline';
      iconClass = 'icon-off';
      stateClass = 'state-off';
      stateText = 'Day off';
      offMessage = 'No shift scheduled today';
    }

    const sBadgeStyle = shiftColor
      ? `style="background:${shiftColor}22;color:${shiftColor}"`
      : 'style="background:color-mix(in srgb,var(--metro-accent) 18%,transparent);color:var(--metro-accent)"';

    const nxtBlock = (label, when, start, end, dateStr, code, lbl, isNow) => {
      if (!dateStr && !start) return '';

      const dim = isNow ? '' : ' dim';

      const times = (start || end)
        ? `
          <div class="nxt-times">
            <span class="nxt-t${dim}">${escapeHtml(formatTime(start))}</span>
            <span class="nxt-arr">→</span>
            <span class="nxt-t${dim}">${escapeHtml(formatTime(end))}</span>
          </div>
        `
        : '';

      const shiftName = englishLabel(code, lbl);
      const dateLine = `${formatDate(dateStr)}${shiftName ? ` · ${shiftName}` : ''}`;

      return `
        <div class="nxt ${isNow ? 'now' : 'then'}">
          <div class="nxt-head">
            <span class="nxt-lbl">${escapeHtml(label)}</span>
            ${when ? `<span class="nxt-when">${escapeHtml(when)}</span>` : ''}
          </div>
          ${times}
          <div class="nxt-date">${escapeHtml(dateLine)}</div>
        </div>
      `;
    };

    const whenText =
      daysAway === 0 ? 'Today' :
      daysAway === 1 ? 'Tomorrow' :
      daysAway !== null ? `In ${daysAway} days` :
      '';

    const showTom = tomDate && tomDate !== nextDate;
    const nxtA = nxtBlock('Up next', whenText, nextStart, nextEnd, nextDate, nextCode, nextLabel, true);
    const nxtB = showTom ? nxtBlock('Then', '', tomStart, tomEnd, tomDate, tomCode, tomLabel, false) : '';

    const hasObBreakdown =
      obCodes.length > 0 ||
      (obToday !== null && obToday > 0) ||
      (obMonthTotal !== null && obMonthTotal > 0);

    const tags = [];

    if (!hasObBreakdown && obMonthTotal > 0) {
      tags.push(`<span class="tag">${escapeHtml(fmtSEK(obMonthTotal))} OB</span>`);
    }

    if (payOncall > 0) {
      tags.push(`<span class="tag">${escapeHtml(fmtSEK(payOncall))} on-call${payOncallHours ? ` · ${escapeHtml(payOncallHours)}h` : ''}</span>`);
    }

    if (payOvertime > 0) {
      tags.push(`<span class="tag">${escapeHtml(fmtSEK(payOvertime))} overtime</span>`);
    }

    if (sickObTotal > 0) {
      tags.push(`<span class="tag warn">${escapeHtml(fmtSEK(sickObTotal))} sick OB</span>`);
    }

    if (paySickDays > 0) {
      tags.push(`<span class="tag warn">${escapeHtml(paySickDays)} sick day${paySickDays !== 1 ? 's' : ''}${paySickHours ? ` · ${escapeHtml(paySickHours)}h` : ''}</span>`);
    }

    if (payVabDays > 0) {
      tags.push(`<span class="tag warn">${escapeHtml(payVabDays)} VAB</span>`);
    }

    if (payLeaveDays > 0) {
      tags.push(`<span class="tag muted">${escapeHtml(payLeaveDays)} leave day${payLeaveDays !== 1 ? 's' : ''}</span>`);
    }

    if (absMonthDed > 0) {
      tags.push(`<span class="tag warn">−${escapeHtml(fmtSEK(absMonthDed))} absence</span>`);
    }

    const obChartTotal = obCodes.reduce((sum, o) => sum + o.pay, 0);
    const obHoursTotal = obMonthHours || obCodes.reduce((sum, o) => sum + o.hours, 0);

    let obCursor = 0;
    const obChartSegments = obCodes.map((o) => {
      const start = obCursor;
      const end = obChartTotal > 0 ? obCursor + ((o.pay / obChartTotal) * 100) : obCursor;
      obCursor = end;
      return `${o.color} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
    });

    const obRowsHtml = obCodes.map((o) => {
      const pct = obChartTotal > 0 ? Math.round((o.pay / obChartTotal) * 100) : 0;

      return `
        <div class="obx">
          <span class="ob-dot" style="background:${escapeHtml(o.color)}"></span>
          <span class="obx-code">${escapeHtml(o.code)}</span>
          <span class="obx-pay">${escapeHtml(fmtSEK(o.pay))}</span>
          <span class="obx-meta">${escapeHtml(fmtNum(o.hours))}h · ${escapeHtml(pct)}%</span>
        </div>
      `;
    }).join('');

    const obBreakdownHtml = hasObBreakdown
      ? `
        <div class="pay-div"></div>
        <div class="ob-panel">
          <div class="ob-summary">
            <div class="ob-title">Monthly OB</div>
            <div class="ob-total">${escapeHtml(fmtSEK(obMonthTotal ?? obChartTotal))}</div>
            <div class="ob-sub">
              ${escapeHtml(fmtNum(obHoursTotal))}h this month${
                obToday !== null && obToday > 0
                  ? ` · Today ${escapeHtml(fmtSEK(obToday))}`
                  : ''
              }
            </div>
          </div>

          <div class="ob-breakdown-wrap">
            ${
              obCodes.length > 1
                ? `
                  <div class="ob-donut" style="background: conic-gradient(${obChartSegments.join(', ')});">
                    <div class="ob-donut-center">
                      <div class="ob-donut-title">OB</div>
                      <div class="ob-donut-value">${escapeHtml(fmtNum(obHoursTotal, 0))}h</div>
                    </div>
                  </div>
                `
                : `<div></div>`
            }

            <div class="ob-breakdown">
              ${obRowsHtml}
            </div>
          </div>
        </div>
      `
      : '';

    shadow.innerHTML = `
      <style>${CARD_CSS}</style>

      <div class="card">
        <div class="hero ${isAbsent ? 'absent' : isWorking ? 'working' : 'off'}">
          <div class="hero-top">
            <div class="hero-id">
              <div class="h-icon ${iconClass}">
                <ha-icon icon="${escapeHtml(iconName)}"></ha-icon>
              </div>
              <div>
                <div class="hero-name">${escapeHtml(title)}</div>
                <div class="hero-state ${stateClass}">${escapeHtml(stateText)}</div>
              </div>
            </div>

            ${
              isWorking && (shiftLabel || shiftCode)
                ? `<span class="hero-badge" ${sBadgeStyle}>${escapeHtml(englishLabel(shiftCode, shiftLabel))}</span>`
                : ''
            }
          </div>

          ${
            isWorking
              ? `
                <div class="hero-times">
                  <div>
                    <div class="ht-big">${escapeHtml(formatTime(shiftStart))}</div>
                    <div class="ht-lbl">Start</div>
                  </div>

                  ${
                    remainTxt
                      ? `
                        <div class="ht-mid">
                          <div class="ht-rem">${escapeHtml(remainTxt)}</div>
                          <div class="ht-rem-lbl">remaining</div>
                        </div>
                      `
                      : `<div class="ht-arrow">→</div>`
                  }

                  <div>
                    <div class="ht-big">${escapeHtml(formatTime(shiftEnd))}</div>
                    <div class="ht-lbl r">End</div>
                  </div>
                </div>

                <div class="prog-track ${pct === null ? 'pnone' : ''}">
                  <div class="prog-fill" style="width:${pct ?? 0}%"></div>
                </div>

                <div class="prog-foot">
                  <span class="pf-txt">
                    ${escapeHtml(
                      pct === 0
                        ? 'Shift not started yet'
                        : pct === 100
                          ? 'Shift complete'
                          : pct !== null
                            ? 'Currently working'
                            : ''
                    )}
                  </span>
                  ${pct !== null ? `<span class="pf-pct">${escapeHtml(pct)}%</span>` : ''}
                </div>
              `
              : `<div class="hero-off-msg">${escapeHtml(offMessage)}</div>`
          }
        </div>

        <div class="body">
          ${
            apiStale
              ? `<div class="stale-banner mb">Periodical API is unreachable. Showing cached schedule data.</div>`
              : ''
          }

          ${
            isWorking
              ? `
                <div class="tile pad mb">
                  <div class="sec-lbl" style="margin-top:0">On shift today</div>
                  ${
                    cwGroups.length === 0
                      ? `<span style="font-size:13px;color:var(--metro-text-secondary)">No co-workers scheduled</span>`
                      : cwGroups.map((g, i) => `
                        ${i > 0 ? '<div class="cw-div"></div>' : ''}
                        <div class="cw-group">
                          <div class="cw-lbl">
                            ${escapeHtml(g.label)}${g.members.some((m) => m.isSelf) ? ' · with you' : ''}
                          </div>
                          <div class="cw-names">
                            ${g.members.map((cw) => {
                              const n = escapeHtml(cwName(cw));
                              return cw.isSelf ? `<span class="cw-you">${n}</span>` : n;
                            }).join(', ')}
                          </div>
                        </div>
                      `).join('')
                  }
                </div>
              `
              : ''
          }

          ${
            nxtA || nxtB
              ? `<div class="upcoming${!nxtB ? ' solo' : ''} mb">${nxtA}${nxtB}</div>`
              : ''
          }

          ${
            shiftsWeek !== null || hoursWeek !== null
              ? `
                <div class="sec-lbl">This week</div>
                <div class="stat-grid mb">
                  ${
                    shiftsWeek !== null
                      ? `
                        <div class="stat">
                          <div class="stat-num">${escapeHtml(shiftsWeek)}<span class="stat-unit">shifts</span></div>
                          <div class="stat-lbl" style="margin-bottom:0">Scheduled</div>
                        </div>
                      `
                      : ''
                  }

                  ${
                    hoursWeek !== null
                      ? `
                        <div class="stat">
                          <div class="stat-num">${escapeHtml(hoursWeek)}<span class="stat-unit">h</span></div>
                          <div class="stat-lbl" style="margin-bottom:0">Hours</div>
                        </div>
                      `
                      : ''
                  }
                </div>
              `
              : ''
          }

          ${
            payNetto !== null || payGross !== null || payHours !== null || workingDays !== null || hasObBreakdown
              ? `
                <div class="sec-lbl">This month</div>
                <div class="tile pad mb" style="padding:16px">
                  <div class="pay-grid">
                    ${
                      payNetto !== null
                        ? `
                          <div>
                            <div class="pay-big">${escapeHtml(fmtSEK(payNetto))}</div>
                            <div class="pay-sub">Net${payGross !== null ? ` · ${escapeHtml(fmtSEK(payGross))} gross` : ''}</div>
                          </div>
                        `
                        : payGross !== null
                          ? `
                            <div>
                              <div class="pay-big">${escapeHtml(fmtSEK(payGross))}</div>
                              <div class="pay-sub">Gross</div>
                            </div>
                          `
                          : '<div></div>'
                    }

                    <div>
                      ${
                        payHours !== null
                          ? `
                            <div class="pay-big">${escapeHtml(payHours)}<span class="stat-unit">h</span></div>
                            <div class="pay-sub">${payShifts !== null ? `${escapeHtml(payShifts)} shifts` : 'Hours worked'}</div>
                          `
                          : workingDays !== null
                            ? `
                              <div class="pay-big">${escapeHtml(workingDays)}<span class="stat-unit">days</span></div>
                              <div class="pay-sub">Scheduled shifts</div>
                            `
                            : ''
                      }
                    </div>
                  </div>

                  ${tags.length ? `<div class="pay-div"></div><div class="pay-tags">${tags.join('')}</div>` : ''}
                  ${obBreakdownHtml}
                </div>
              `
              : ''
          }

          ${
            shiftsRemaining !== null || shiftsYear !== null || hoursYear !== null || vacRem !== null || absences !== null
              ? `
                <div class="sec-lbl">This year</div>
                <div class="stat-grid mb">
                  ${
                    shiftsRemaining !== null
                      ? `
                        <div class="stat">
                          <div class="stat-num">${escapeHtml(shiftsRemaining)}<span class="stat-unit">left</span></div>
                          <div class="stat-lbl">Shifts remaining</div>
                          ${
                            yearPct !== null
                              ? `<div class="stat-bar"><div class="stat-bar-fill bar-accent" style="width:${yearPct}%"></div></div>`
                              : ''
                          }
                        </div>
                      `
                      : ''
                  }

                  ${
                    shiftsYear !== null
                      ? `
                        <div class="stat">
                          <div class="stat-num">${escapeHtml(shiftsYear)}<span class="stat-unit">total</span></div>
                          <div class="stat-lbl" style="margin-bottom:0">Shifts this year</div>
                        </div>
                      `
                      : ''
                  }

                  ${
                    hoursYear !== null
                      ? `
                        <div class="stat">
                          <div class="stat-num">${escapeHtml(hoursYear)}<span class="stat-unit">h</span></div>
                          <div class="stat-lbl" style="margin-bottom:0">Hours this year</div>
                        </div>
                      `
                      : ''
                  }

                  ${
                    absences !== null
                      ? `
                        <div class="stat">
                          <div class="stat-num">${escapeHtml(absences)}</div>
                          <div class="stat-lbl" style="margin-bottom:0">Absences</div>
                        </div>
                      `
                      : ''
                  }
                </div>

                ${
                  vacRem !== null
                    ? `
                      <div class="sec-lbl">Vacation</div>
                      <div class="tile pad mb">
                        <div class="vac-row">
                          <div>
                            <span class="vac-num">${escapeHtml(vacRem)}</span>
                            <span class="vac-unit">days left</span>
                          </div>

                          <div class="vac-right">
                            <div class="vac-sub">${escapeHtml(vacUsed ?? 0)} of ${escapeHtml(vacTotal ?? '?')} days used</div>

                            ${
                              vacPct !== null
                                ? `<div class="stat-bar"><div class="stat-bar-fill ${vacPct >= 75 ? 'bar-warning' : 'bar-success'}" style="width:${vacPct}%"></div></div>`
                                : ''
                            }

                            ${
                              vacPayout > 0
                                ? `<div class="stat-note">≈ ${escapeHtml(fmtSEK(vacPayout))} payout${vacDaysToSave > 0 ? ` · ${escapeHtml(vacDaysToSave)} to save` : ''}</div>`
                                : ''
                            }
                          </div>
                        </div>
                      </div>
                    `
                    : ''
                }
              `
              : ''
          }
        </div>

        <div class="footer-bar">
          <span class="footer-item">Updated ${escapeHtml(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }))}</span>
          ${rotWeek ? `<span class="footer-item">Rotation week ${escapeHtml(rotWeek)}</span>` : ''}
          <span class="footer-item" style="opacity:.5">v${escapeHtml(CARD_VERSION)}</span>
        </div>
      </div>
    `;
  }
}

class PeriodicalCardEditor extends HTMLElement {
  setConfig(c) {
    this._config = c || {};
    this._render();
  }

  _fire(c) {
    this.dispatchEvent(new CustomEvent('config-changed', {
      detail: { config: c },
      bubbles: true,
      composed: true,
    }));
  }

  _render() {
    const c = this._config || {};

    this.innerHTML = `
      <div style="padding:4px 0">
        <div style="margin-bottom:14px">
          <label style="font-size:12px;color:var(--secondary-text-color);display:block;margin-bottom:4px">
            Card title optional
          </label>
          <input id="n" type="text" value="${escapeHtml(c.name ?? '')}" placeholder="Auto: uses user name"
            style="width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.08);background:#243447;color:#fff;font-size:13px;font-family:'Segoe UI',system-ui,sans-serif;" />
        </div>

        <div style="margin-bottom:14px">
          <label style="font-size:12px;color:var(--secondary-text-color);display:block;margin-bottom:4px">
            User prefix only needed with multiple Periodical users
          </label>
          <input id="p" type="text" value="${escapeHtml(c.user_prefix ?? '')}" placeholder="periodical"
            style="width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid rgba(255,255,255,.08);background:#243447;color:#fff;font-size:13px;font-family:'Segoe UI',system-ui,sans-serif;" />
          <div style="font-size:11px;color:var(--secondary-text-color);margin-top:4px">
            Example: <code>periodical</code> maps to <code>sensor.periodical_shift_start_today</code>
          </div>
        </div>

        <div style="font-size:11px;color:#cbd5e1;padding:12px 14px;border-radius:8px;background:#243447;border-left:3px solid #0ea5e9;font-family:'Segoe UI',system-ui,sans-serif;">
          Entities are auto-discovered. Leave the prefix empty unless you have more than one Periodical user.
        </div>
      </div>
    `;

    this.querySelector('#n').addEventListener('change', (e) => {
      this._fire({ ...c, name: e.target.value || undefined });
    });

    this.querySelector('#p').addEventListener('change', (e) => {
      this._fire({ ...c, user_prefix: e.target.value || undefined });
    });
  }
}

customElements.define('periodical-card', PeriodicalCard);
customElements.define('periodical-card-editor', PeriodicalCardEditor);

window.customCards = window.customCards ?? [];
window.customCards.push({
  type: 'periodical-card',
  name: 'Periodical',
  description: 'Work schedule, shifts, pay, vacation and year overview from the Periodical integration.',
  preview: true,
  editor: 'periodical-card-editor',
});
