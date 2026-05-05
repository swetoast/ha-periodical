/**
 * periodical-card.js  v3.0
 * Custom Lovelace card for the Periodical Home Assistant integration.
 *
 * Install : copy to <config>/www/periodical-card.js
 * Resource: /local/periodical-card.js  (type: module)
 *
 * Minimal config (auto-discovers all entities):
 *   type: custom:periodical-card
 *
 * Optional title override:
 *   name: My Schedule
 */

// ─────────────────────────────────────────────────────────────────────────────
//  Entity discovery
// ─────────────────────────────────────────────────────────────────────────────
function discoverPrefix(hass, forced) {
  if (forced) return forced;
  for (const eid of Object.keys(hass.states)) {
    if (
      eid.startsWith('binary_sensor.') &&
      eid.endsWith('_working_today') &&
      hass.states[eid]?.attributes?.device_class === 'occupancy'
    ) {
      return eid.slice('binary_sensor.'.length, -'_working_today'.length);
    }
  }
  return null;
}

const ENTITY_MAP = {
  working_today:        { domain: 'binary_sensor', suffix: 'working_today'           },
  absent_today:         { domain: 'binary_sensor', suffix: 'absent_today'             },
  shift_start:          { domain: 'sensor',        suffix: 'shift_start_today'        },
  shift_end:            { domain: 'sensor',        suffix: 'shift_end_today'          },
  status_today:         { domain: 'sensor',        suffix: 'status_today'             },
  coworkers_today:      { domain: 'sensor',        suffix: 'co_workers_today'         },
  ob_today:             { domain: 'sensor',        suffix: 'ob_supplement_today'      },
  rotation_week:        { domain: 'sensor',        suffix: 'rotation_week'            },
  next_shift_date:      { domain: 'sensor',        suffix: 'next_shift_date'          },
  next_shift_start:     { domain: 'sensor',        suffix: 'next_shift_start'         },
  next_shift_end:       { domain: 'sensor',        suffix: 'next_shift_end'           },
  tomorrow_date:        { domain: 'sensor',        suffix: 'tomorrow_shift_date'      },
  tomorrow_start:       { domain: 'sensor',        suffix: 'tomorrow_shift_start'     },
  tomorrow_end:         { domain: 'sensor',        suffix: 'tomorrow_shift_end'       },
  shifts_week:          { domain: 'sensor',        suffix: 'shifts_this_week'         },
  hours_week:           { domain: 'sensor',        suffix: 'hours_this_week'          },
  working_days:         { domain: 'sensor',        suffix: 'working_days_this_month'  },
  pay_gross:            { domain: 'sensor',        suffix: 'monthly_pay_gross'        },
  pay_netto:            { domain: 'sensor',        suffix: 'monthly_pay_netto'        },
  pay_hours:            { domain: 'sensor',        suffix: 'monthly_hours_worked'     },
  pay_shifts:           { domain: 'sensor',        suffix: 'monthly_shifts'           },
  pay_oncall:           { domain: 'sensor',        suffix: 'monthly_on_call_pay'      },
  pay_oncall_hours:     { domain: 'sensor',        suffix: 'monthly_on_call_hours'    },
  pay_overtime:         { domain: 'sensor',        suffix: 'monthly_overtime_pay'     },
  pay_sick_days:        { domain: 'sensor',        suffix: 'monthly_sick_days'        },
  pay_sick_hours:       { domain: 'sensor',        suffix: 'monthly_sick_hours'       },
  pay_vab_days:         { domain: 'sensor',        suffix: 'monthly_vab_days'         },
  pay_leave_days:       { domain: 'sensor',        suffix: 'monthly_leave_days'       },
  shifts_year:          { domain: 'sensor',        suffix: 'shifts_this_year'         },
  shifts_remaining:     { domain: 'sensor',        suffix: 'shifts_remaining_this_year'},
  hours_year:           { domain: 'sensor',        suffix: 'hours_this_year'          },
  vacation_remaining:   { domain: 'sensor',        suffix: 'vacation_days_remaining'  },
  vacation_total:       { domain: 'sensor',        suffix: 'vacation_days_total'      },
  vacation_used:        { domain: 'sensor',        suffix: 'vacation_days_used'       },
  absences:             { domain: 'sensor',        suffix: 'absences_this_year'       },
};

/** Parse "YYYY-MM-DD" as LOCAL midnight — new Date("YYYY-MM-DD") is UTC and
 *  shifts the date by the local offset (e.g. Sweden UTC+2 → wrong day). */
function parseLocalDate(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return null;
  const m = str.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}

/** ISO datetime with tz-offset OR plain HH:MM → Date */
function parseTime(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return null;
  const hm = str.match(/^(\d{1,2}):(\d{2})$/);
  if (hm) { const d = new Date(); d.setHours(+hm[1], +hm[2], 0, 0); return d; }
  const iso = new Date(str);
  return isNaN(iso) ? null : iso;
}

function formatTime(str) {
  const d = parseTime(str);
  return d ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '--:--';
}

function formatDate(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return '—';
  const d = parseLocalDate(str) || new Date(str);
  return isNaN(d) ? str : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

function daysUntil(str) {
  const target = parseLocalDate(str);
  if (!target) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((target - today) / 86400000);
}

/** Convert a plain HH:MM string to minutes since midnight. */
function toLocalMins(str) {
  if (!str || str === 'unknown' || str === 'unavailable') return null;
  const m = str.match(/^(\d{1,2}):(\d{2})/);
  if (!m) return null;
  return +m[1] * 60 + +m[2];
}

function shiftProgress(startStr, endStr) {
  const now = new Date();
  const nowMins   = now.getHours() * 60 + now.getMinutes();
  const startMins = toLocalMins(startStr);
  const endMins   = toLocalMins(endStr);
  if (startMins === null || endMins === null) return null;

  const overnight = endMins <= startMins; // e.g. 22:00 -> 06:30

  let elapsed, duration;
  if (!overnight) {
    if (nowMins < startMins) return 0;
    if (nowMins >= endMins)  return 100;
    elapsed  = nowMins - startMins;
    duration = endMins - startMins;
  } else {
    // Overnight: duration spans midnight
    duration = (1440 - startMins) + endMins;
    if (nowMins >= startMins) {
      // Pre-midnight — first half of shift
      elapsed = nowMins - startMins;
    } else if (nowMins < endMins) {
      // Post-midnight — second half of shift
      elapsed = (1440 - startMins) + nowMins;
    } else {
      // Past end time
      return 100;
    }
  }
  return Math.round((elapsed / duration) * 100);
}

function fmtSEK(val) {
  return Number(val).toLocaleString('sv-SE', { style:'currency', currency:'SEK', maximumFractionDigits:0 });
}

const SHIFT_GROUPS = [
  { key:'day',      label:'Day shift',     match:(c,l) => /dag/i.test(l)       || /^D/i.test(c)  },
  { key:'evening',  label:'Evening shift', match:(c,l) => /kv[äa]ll/i.test(l) || /^(E|K)/i.test(c) },
  { key:'night',    label:'Night shift',   match:(c,l) => /natt/i.test(l)      || /^N/i.test(c)  },
  { key:'oncall',   label:'On call',       match:(c,l) => /^OC$/i.test(c)      || /beredskap|on.?call|jour/i.test(l) },
  { key:'overtime', label:'Overtime',      match:(c,l) => /^OT$/i.test(c)      || /övertid|overtime|extra/i.test(l) },
];

function classifyShift(code, label) {
  for (const g of SHIFT_GROUPS) if (g.match(code||'', label||'')) return g;
  return { key:'other', label: label||code||'Other' };
}

function groupCoworkers(members) {
  const map = {};
  for (const cw of members) {
    const g = classifyShift(cw.shift_code, cw.shift_label);
    if (!map[g.key]) map[g.key] = { ...g, members:[] };
    map[g.key].members.push(cw);
  }
  return ['day','evening','night','oncall','overtime','other'].map(k => map[k]).filter(Boolean);
}

function cwName(cw) { return typeof cw === 'string' ? cw : (cw?.name || '?'); }

const CARD_CSS = `
  :host { --r:12px; --rs:8px; --gap:14px; display:block; }

  .card {
    background:var(--card-background-color);
    border-radius:var(--ha-card-border-radius,var(--r));
    padding:20px;
    font-family:var(--primary-font-family,'Roboto',sans-serif);
    color:var(--primary-text-color);
    overflow:hidden;
  }

  /* Header */
  .header { display:flex; align-items:center; justify-content:space-between; margin-bottom:18px; }
  .header-left { display:flex; align-items:center; gap:10px; }
  .h-icon { width:36px; height:36px; border-radius:var(--rs); display:flex; align-items:center; justify-content:center; font-size:18px; }
  .icon-working { background:color-mix(in srgb,var(--success-color) 16%,transparent); }
  .icon-off     { background:color-mix(in srgb,var(--secondary-text-color) 12%,transparent); }
  .icon-absent  { background:color-mix(in srgb,var(--error-color) 14%,transparent); }
  .card-title { font-size:15px; font-weight:500; }
  .h-right { display:flex; align-items:center; gap:8px; }
  .rot-badge { font-size:11px; color:var(--secondary-text-color); padding:3px 8px; border-radius:99px; background:var(--secondary-background-color); }
  .status-pill { font-size:11px; font-weight:600; letter-spacing:.6px; text-transform:uppercase; padding:4px 10px; border-radius:99px; line-height:1; }
  .pill-working { background:color-mix(in srgb,var(--success-color) 16%,transparent); color:var(--success-color); }
  .pill-off     { background:color-mix(in srgb,var(--secondary-text-color) 12%,transparent); color:var(--secondary-text-color); }
  .pill-absent  { background:color-mix(in srgb,var(--error-color) 14%,transparent); color:var(--error-color); }

  /* Section label */
  .sec-lbl { font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.7px; color:var(--secondary-text-color); margin-bottom:8px; }

  /* Today shift */
  .shift-block { background:var(--secondary-background-color); border-radius:var(--r); padding:14px 16px; margin-bottom:var(--gap); }
  .shift-times { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; }
  .s-time { font-size:22px; font-weight:500; }
  .s-arrow { font-size:13px; color:var(--secondary-text-color); }
  .s-sub { font-size:11px; color:var(--secondary-text-color); margin-top:1px; }
  .s-sub.r { text-align:right; }
  .s-badge { display:inline-block; font-size:11px; font-weight:600; padding:2px 7px; border-radius:99px; margin-left:4px; background:color-mix(in srgb,var(--primary-color) 12%,transparent); color:var(--primary-color); }
  .prog-track { height:6px; border-radius:3px; background:var(--divider-color); position:relative; overflow:visible; margin-bottom:8px; }
  .prog-fill  { height:100%; border-radius:3px; background:var(--primary-color); transition:width .6s; position:relative; }
  .prog-fill::after { content:''; position:absolute; right:-5px; top:-3px; width:12px; height:12px; border-radius:50%; background:var(--primary-color); box-shadow:0 0 0 3px var(--card-background-color); }
  .pnone .prog-fill::after { display:none; }
  .s-foot { display:flex; justify-content:space-between; align-items:center; }
  .s-foot-txt { font-size:12px; color:var(--secondary-text-color); }
  .s-pct { font-size:12px; font-weight:500; color:var(--primary-color); }
  .no-shift { text-align:center; padding:8px 0 4px; color:var(--secondary-text-color); font-size:13px; }

  /* Co-workers */
  .cw-section { background:var(--secondary-background-color); border-radius:var(--r); padding:12px 14px; margin-bottom:var(--gap); }
  .cw-group { margin-bottom:8px; }
  .cw-group:last-child { margin-bottom:0; }
  .cw-div { height:1px; background:var(--divider-color); margin:8px 0; }
  .cw-lbl { font-size:11px; font-weight:600; color:var(--secondary-text-color); margin-bottom:2px; }
  .cw-names { font-size:13px; line-height:1.5; }
  .cw-you { font-weight:600; }

  /* OB */
  .ob-row { background:var(--secondary-background-color); border-radius:var(--rs); padding:10px 14px; display:flex; align-items:center; justify-content:space-between; margin-bottom:var(--gap); }
  .ob-lbl { font-size:12px; color:var(--secondary-text-color); }
  .ob-val { font-size:14px; font-weight:600; color:var(--primary-color); }

  /* Up next + Tomorrow side by side */
  .upcoming { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:var(--gap); }
  .upcoming.solo { grid-template-columns:1fr; }
  .nxt-block { background:var(--secondary-background-color); border-radius:var(--r); padding:12px 14px; }
  .nxt-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:6px; }
  .nxt-when { font-size:11px; font-weight:600; padding:2px 7px; border-radius:99px; background:color-mix(in srgb,var(--primary-color) 12%,transparent); color:var(--primary-color); }
  .nxt-times { display:flex; align-items:baseline; gap:5px; }
  .nxt-t { font-size:17px; font-weight:500; }
  .nxt-arr { font-size:11px; color:var(--secondary-text-color); }
  .nxt-row { display:flex; align-items:flex-end; justify-content:space-between; }
  .nxt-date { font-size:11px; color:var(--secondary-text-color); margin-top:2px; }
  .nxt-badge { font-size:11px; font-weight:600; padding:2px 6px; border-radius:99px; }

  /* Generic section */
  .section { background:var(--secondary-background-color); border-radius:var(--r); padding:12px 14px; margin-bottom:var(--gap); }

  /* 2-col key-value grid */
  .kv { display:grid; grid-template-columns:1fr 1fr; gap:10px 14px; }
  .kv-val  { font-size:18px; font-weight:500; line-height:1; }
  .kv-unit { font-size:11px; color:var(--secondary-text-color); margin-left:2px; }
  .kv-lbl  { font-size:11px; color:var(--secondary-text-color); margin-top:2px; }
  .bar-wrap { height:3px; background:var(--divider-color); border-radius:2px; margin-top:6px; overflow:hidden; }
  .bar-fill { height:100%; border-radius:2px; background:var(--primary-color); }

  /* Month pay */
  .pay-grid { display:grid; grid-template-columns:1fr 1fr; gap:0; }
  .pay-big  { font-size:16px; font-weight:500; line-height:1; }
  .pay-sub  { font-size:11px; color:var(--secondary-text-color); margin-top:2px; }
  .pay-div  { height:1px; background:var(--divider-color); margin:10px 0; }
  .pay-tags { display:flex; flex-wrap:wrap; gap:6px; }
  .tag      { font-size:11px; font-weight:600; padding:3px 8px; border-radius:99px; background:color-mix(in srgb,var(--primary-color) 12%,transparent); color:var(--primary-color); }
  .tag.warn { background:color-mix(in srgb,var(--error-color) 12%,transparent); color:var(--error-color); }
  .tag.muted{ background:var(--divider-color); color:var(--secondary-text-color); }

  /* Vacation */
  .vac-row { display:flex; align-items:center; gap:14px; }
  .vac-num { font-size:24px; font-weight:500; line-height:1; white-space:nowrap; }
  .vac-unit{ font-size:12px; color:var(--secondary-text-color); margin-left:2px; }
  .vac-right{ flex:1; }
  .vac-sub { font-size:11px; color:var(--secondary-text-color); margin-bottom:5px; }
  .vac-bar-wrap { height:6px; background:var(--divider-color); border-radius:3px; overflow:hidden; }
  .vac-bar-fill { height:100%; border-radius:3px; background:var(--primary-color); }

  /* Footer */
  .footer { text-align:right; font-size:10px; color:var(--secondary-text-color); margin-top:4px; opacity:.5; }

  /* Not found */
  .not-found { text-align:center; padding:24px 16px; color:var(--secondary-text-color); font-size:13px; line-height:1.7; }
`;

// ─────────────────────────────────────────────────────────────────────────────
//  Card element
// ─────────────────────────────────────────────────────────────────────────────
class PeriodicalCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass   = null;
    this._timer  = null;
  }

  static getStubConfig() { return {}; }
  setConfig(c) { this._config = c || {}; this._render(); }
  set hass(h)  { this._hass = h; this._render(); }
  getCardSize() { return 7; }
  connectedCallback()    { this._timer = setInterval(() => this._render(), 60_000); }
  disconnectedCallback() { clearInterval(this._timer); }

  _prefix() { return this._hass ? discoverPrefix(this._hass, this._config.user_prefix ?? null) : null; }
  _eid(k)   { const ov=this._config?.entities?.[k]; if(ov) return ov; const p=this._prefix(); if(!p) return null; const e=ENTITY_MAP[k]; return e?`${e.domain}.${p}_${e.suffix}`:null; }
  _state(k) { const eid=this._eid(k); return (eid&&this._hass)?(this._hass.states[eid]??null):null; }
  _val(k)   { const s=this._state(k); if(!s) return null; return (s.state==='unknown'||s.state==='unavailable')?null:s.state; }
  _num(k)   { const v=parseFloat(this._val(k)); return isNaN(v)?null:v; }
  _attr(k,a){ return this._state(k)?.attributes?.[a]??null; }

  _render() {
    const shadow = this.shadowRoot;
    if (!this._hass) { shadow.innerHTML = ''; return; }

    const prefix = this._prefix();
    if (!prefix) {
      shadow.innerHTML = `<style>${CARD_CSS}</style><div class="card"><div class="not-found">🔍 No Periodical entities found.<br>Make sure the integration is installed.</div></div>`;
      return;
    }

    // ── Data ─────────────────────────────────────────────────────────────────
    const isWorking   = this._val('working_today') === 'on';
    const isAbsent    = this._val('absent_today')  === 'on';
    // Keep display name generic by default. Use `name:` in card config if you want a custom title.
    const title = this._config.name || 'User';
    const rotWeek     = this._val('rotation_week');

    const shiftStart  = this._val('shift_start');
    const shiftEnd    = this._val('shift_end');
    const shiftLabel  = this._attr('shift_start','shift_label') || this._attr('shift_end','shift_label');
    const shiftCode   = this._attr('shift_start','shift_code')  || this._attr('shift_end','shift_code');
    const shiftColor  = this._attr('shift_start','shift_color') || this._attr('shift_end','shift_color');
    // Use plain HH:MM attributes from the API — these are always local Sweden
    // time and never have UTC offset confusion like the ISO sensor state does.
    const progressStart = this._attr('shift_start','start_time') || shiftStart;
    const progressEnd   = this._attr('shift_start','end_time')   || shiftEnd;
    const pct           = isWorking ? shiftProgress(progressStart, progressEnd) : null;

    const coworkersRaw = this._attr('coworkers_today','co_workers') ?? [];
    const coworkers    = Array.isArray(coworkersRaw) ? coworkersRaw : [];
    const selfEntry    = (isWorking && shiftCode)
      ? { name:title, shift_code:shiftCode, shift_label:shiftLabel||shiftCode, isSelf:true } : null;
    const cwGroups     = groupCoworkers(selfEntry ? [selfEntry,...coworkers] : coworkers);

    const nextDate  = this._val('next_shift_date');
    const nextStart = this._val('next_shift_start') || this._attr('next_shift_date','start_time');
    const nextEnd   = this._val('next_shift_end')   || this._attr('next_shift_date','end_time');
    const nextCode  = this._attr('next_shift_date','shift_code');
    const nextLabel = this._attr('next_shift_date','shift_label');
    const nextColor = this._attr('next_shift_date','shift_color');
    const daysAway  = daysUntil(nextDate);

    const tomDate   = this._val('tomorrow_date');
    const tomStart  = this._val('tomorrow_start')  || this._attr('tomorrow_date','start_time');
    const tomEnd    = this._val('tomorrow_end')     || this._attr('tomorrow_date','end_time');
    const tomCode   = this._attr('tomorrow_date','shift_code');
    const tomLabel  = this._attr('tomorrow_date','shift_label');
    const tomColor  = this._attr('tomorrow_date','shift_color');

    const shiftsWeek  = this._num('shifts_week');
    const hoursWeek   = this._num('hours_week');

    const payGross        = this._num('pay_gross');
    const payNetto        = this._num('pay_netto');
    const payHours        = this._num('pay_hours');
    const payShifts       = this._num('pay_shifts');
    const workingDays     = this._num('working_days');
    const payOncall       = this._num('pay_oncall');
    const payOncallHours  = this._num('pay_oncall_hours');
    const payOvertime     = this._num('pay_overtime');
    const paySickDays     = this._num('pay_sick_days');
    const paySickHours    = this._num('pay_sick_hours');
    const payVabDays      = this._num('pay_vab_days');
    const payLeaveDays    = this._num('pay_leave_days');
    const obTotal         = this._num('ob_today');

    const shiftsYear      = this._num('shifts_year');
    const shiftsRemaining = this._num('shifts_remaining');
    const hoursYear       = this._num('hours_year');
    const yearPct         = (shiftsYear && shiftsRemaining !== null)
      ? Math.round(((shiftsYear - shiftsRemaining) / shiftsYear) * 100) : null;

    const vacRem    = this._num('vacation_remaining');
    const vacTotal  = this._num('vacation_total');
    const vacUsed   = this._num('vacation_used');
    const vacPct    = (vacTotal > 0) ? Math.round(((vacUsed ?? 0) / vacTotal) * 100) : null;
    const absences  = this._num('absences');

    // ── Header ───────────────────────────────────────────────────────────────
    let iconEmoji, iconClass, pillClass, pillText;
    if (isAbsent)       { iconEmoji='🤒'; iconClass='icon-absent'; pillClass='pill-absent'; pillText='Absent'; }
    else if (isWorking) { iconEmoji='💼'; iconClass='icon-working'; pillClass='pill-working'; pillText='Working'; }
    else                { iconEmoji='🏡'; iconClass='icon-off'; pillClass='pill-off'; pillText='Day Off'; }

    // ── Today shift ──────────────────────────────────────────────────────────
    const sBadgeStyle = shiftColor ? `style="background:${shiftColor}22;color:${shiftColor}"` : '';
    const sBadge = (shiftLabel||shiftCode) ? `<span class="s-badge" ${sBadgeStyle}>${shiftLabel||shiftCode}</span>` : '';

    // ── Next/Tomorrow block builder ──────────────────────────────────────────
    const nxtBlock = (label, when, start, end, dateStr, code, lbl, color) => {
      if (!dateStr && !start) return '';
      const bs = color
        ? `background:${color}22;color:${color}`
        : `background:color-mix(in srgb,var(--primary-color) 12%,transparent);color:var(--primary-color)`;
      const badge = (lbl||code) ? `<span class="nxt-badge" style="${bs}">${lbl||code}</span>` : '';
      const times = (start||end)
        ? `<div class="nxt-times"><span class="nxt-t">${formatTime(start)}</span><span class="nxt-arr">→</span><span class="nxt-t">${formatTime(end)}</span></div>` : '';
      return `<div class="nxt-block">
        <div class="nxt-head">
          <span class="sec-lbl" style="margin-bottom:0">${label}</span>
          ${when?`<span class="nxt-when">${when}</span>`:''}
        </div>
        <div class="nxt-row">
          <div>${times}<div class="nxt-date">${formatDate(dateStr)}</div></div>
          ${badge}
        </div>
      </div>`;
    };

    const whenText  = daysAway===0?'Today':daysAway===1?'Tomorrow':daysAway!==null?`In ${daysAway} days`:'';
    const showTom   = tomDate && tomDate !== nextDate;
    const nxtA      = nxtBlock('Up next', whenText, nextStart, nextEnd, nextDate, nextCode, nextLabel, nextColor);
    const nxtB      = showTom ? nxtBlock('Tomorrow','', tomStart, tomEnd, tomDate, tomCode, tomLabel, tomColor) : '';

    // ── Pay extras tags ──────────────────────────────────────────────────────
    const tags = [];
    if (payOncall    > 0) tags.push(`<span class="tag">${fmtSEK(payOncall)} on-call${payOncallHours?` · ${payOncallHours}h`:''}</span>`);
    if (payOvertime  > 0) tags.push(`<span class="tag">${fmtSEK(payOvertime)} overtime</span>`);
    if (paySickDays  > 0) tags.push(`<span class="tag warn">${paySickDays} sick day${paySickDays!==1?'s':''}${paySickHours?` · ${paySickHours}h`:''}</span>`);
    if (payVabDays   > 0) tags.push(`<span class="tag warn">${payVabDays} VAB</span>`);
    if (payLeaveDays > 0) tags.push(`<span class="tag muted">${payLeaveDays} leave day${payLeaveDays!==1?'s':''}</span>`);

    // ── Compose ──────────────────────────────────────────────────────────────
    shadow.innerHTML = `
    <style>${CARD_CSS}</style>
    <div class="card">

      <!-- Header -->
      <div class="header">
        <div class="header-left">
          <div class="h-icon ${iconClass}">${iconEmoji}</div>
          <div class="card-title">${title}</div>
        </div>
        <div class="h-right">
          ${rotWeek ? `<span class="rot-badge">Rotation ${rotWeek}</span>` : ''}
          <div class="status-pill ${pillClass}">${pillText}</div>
        </div>
      </div>

      <!-- Today's shift -->
      <div class="shift-block">
        ${isWorking ? `
          <div class="shift-times">
            <div>
              <div class="s-time">${formatTime(shiftStart)}</div>
              <div class="s-sub">Start ${sBadge}</div>
            </div>
            <div class="s-arrow">→</div>
            <div style="text-align:right">
              <div class="s-time">${formatTime(shiftEnd)}</div>
              <div class="s-sub r">End</div>
            </div>
          </div>
          <div class="prog-track ${pct===null?'pnone':''}">
            <div class="prog-fill" style="width:${pct??0}%"></div>
          </div>
          <div class="s-foot">
            <span class="s-foot-txt">${pct===0?'Shift not started yet':pct===100?'Shift complete':pct!==null?'Currently working':''}</span>
            ${pct!==null?`<span class="s-pct">${pct}%</span>`:''}
          </div>`
        : `<div class="no-shift">${isAbsent?'🤒 Absent today':'✓ No shift today'}</div>`}
      </div>

      <!-- Co-workers (only when working) -->
      ${isWorking ? `
        <div class="cw-section">
          <div class="sec-lbl">On shift today</div>
          ${cwGroups.length === 0
            ? `<span style="font-size:13px;color:var(--secondary-text-color)">No co-workers scheduled</span>`
            : cwGroups.map((g,i) => `
                ${i>0?'<div class="cw-div"></div>':''}
                <div class="cw-group">
                  <div class="cw-lbl">${g.label}</div>
                  <div class="cw-names">${g.members.map(cw => {
                    const n = cwName(cw);
                    return cw.isSelf ? `<span class="cw-you">${n}</span>` : n;
                  }).join(', ')}</div>
                </div>`).join('')}
        </div>` : ''}

      <!-- OB supplement -->
      ${(obTotal!==null&&obTotal>0) ? `
        <div class="ob-row">
          <span class="ob-lbl">💶 OB supplement today</span>
          <span class="ob-val">${fmtSEK(obTotal)}</span>
        </div>` : ''}

      <!-- Up next + Tomorrow -->
      ${(nxtA||nxtB) ? `<div class="upcoming${!nxtB?' solo':''}">${nxtA}${nxtB}</div>` : ''}

      <!-- This week -->
      ${(shiftsWeek!==null||hoursWeek!==null) ? `
        <div class="section">
          <div class="sec-lbl">This week</div>
          <div class="kv">
            ${shiftsWeek!==null?`<div><div class="kv-val">${shiftsWeek}<span class="kv-unit">shifts</span></div><div class="kv-lbl">Scheduled shifts</div></div>`:''}
            ${hoursWeek!==null?`<div><div class="kv-val">${hoursWeek}<span class="kv-unit">h</span></div><div class="kv-lbl">Scheduled hours</div></div>`:''}
          </div>
        </div>` : ''}

      <!-- This month -->
      ${(payGross!==null||payHours!==null||workingDays!==null) ? `
        <div class="section">
          <div class="sec-lbl">This month</div>
          <div class="pay-grid">
            ${payNetto!==null?`
              <div>
                <div class="pay-big">${fmtSEK(payNetto)}</div>
                <div class="pay-sub">${payGross!==null?`${fmtSEK(payGross)} Netto`:''}</div>
              </div>` : payGross!==null?`
              <div>
                <div class="pay-big">${fmtSEK(payGross)}</div>
                <div class="pay-sub">Netto</div>
              </div>`:``}
            <div>
              ${payHours!==null?`<div class="pay-big">${payHours}<span class="kv-unit">h</span></div><div class="pay-sub">${payShifts!==null?`${payShifts} shifts`:'Hours worked'}</div>`:''}
              ${workingDays!==null&&payHours===null?`<div class="pay-big">${workingDays}<span class="kv-unit">days</span></div><div class="pay-sub">Scheduled shifts</div>`:''}
            </div>
          </div>
          ${tags.length?`<div class="pay-div"></div><div class="pay-tags">${tags.join('')}</div>`:''}
        </div>` : ''}

      <!-- This year -->
      ${(shiftsYear!==null||hoursYear!==null) ? `
        <div class="section">
          <div class="sec-lbl">This year</div>
          <div class="kv">
            ${shiftsRemaining!==null?`
              <div>
                <div class="kv-val">${shiftsRemaining}<span class="kv-unit">left</span></div>
                <div class="kv-lbl">Shifts remaining</div>
                ${yearPct!==null?`<div class="bar-wrap"><div class="bar-fill" style="width:${yearPct}%"></div></div>`:''}
              </div>`:'' }
            ${shiftsYear!==null?`
              <div>
                <div class="kv-val">${shiftsYear}<span class="kv-unit">total</span></div>
                <div class="kv-lbl">Shifts this year</div>
              </div>`:''}
            ${hoursYear!==null?`
              <div>
                <div class="kv-val">${hoursYear}<span class="kv-unit">h</span></div>
                <div class="kv-lbl">Hours this year</div>
              </div>`:''}
            ${absences!==null?`
              <div>
                <div class="kv-val">${absences}</div>
                <div class="kv-lbl">Absences this year</div>
              </div>`:''}
          </div>
        </div>` : ''}

      <!-- Vacation -->
      ${vacRem!==null ? `
        <div class="section">
          <div class="sec-lbl">Vacation</div>
          <div class="vac-row">
            <div><span class="vac-num">🏖️ ${vacRem}</span><span class="vac-unit">days left</span></div>
            <div class="vac-right">
              <div class="vac-sub">${vacUsed??0} of ${vacTotal??'?'} days used</div>
              ${vacPct!==null?`<div class="vac-bar-wrap"><div class="vac-bar-fill" style="width:${vacPct}%"></div></div>`:''}
            </div>
          </div>
        </div>` : ''}

      <div class="footer">Updated ${new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</div>
    </div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
//  Config editor
// ─────────────────────────────────────────────────────────────────────────────
class PeriodicalCardEditor extends HTMLElement {
  setConfig(c) { this._config = c || {}; this._render(); }
  _fire(c) { this.dispatchEvent(new CustomEvent('config-changed',{detail:{config:c},bubbles:true,composed:true})); }
  _render() {
    const c = this._config;
    this.innerHTML = `
      <div style="padding:4px 0">
        <div style="margin-bottom:14px">
          <label style="font-size:12px;color:var(--secondary-text-color);display:block;margin-bottom:4px">Card title (optional)</label>
          <input id="n" type="text" value="${c.name??''}" placeholder="Auto: uses user name"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);font-size:13px;"/>
        </div>
        <div style="margin-bottom:14px">
          <label style="font-size:12px;color:var(--secondary-text-color);display:block;margin-bottom:4px">User prefix (only needed with multiple Periodical users)</label>
          <input id="p" type="text" value="${c.user_prefix??''}" placeholder="e.g. user"
            style="width:100%;box-sizing:border-box;padding:8px 10px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color);font-size:13px;"/>
          <div style="font-size:11px;color:var(--secondary-text-color);margin-top:4px">e.g. <code>user</code> → <code>sensor.user_shift_start_today</code></div>
        </div>
        <div style="font-size:11px;color:var(--secondary-text-color);padding:10px 12px;border-radius:6px;background:var(--secondary-background-color)">
           All entities are auto-discovered — no manual mapping needed.
        </div>
      </div>`;
    this.querySelector('#n').addEventListener('change', e => this._fire({...c, name:e.target.value||undefined}));
    this.querySelector('#p').addEventListener('change', e => this._fire({...c, user_prefix:e.target.value||undefined}));
  }
}

customElements.define('periodical-card', PeriodicalCard);
customElements.define('periodical-card-editor', PeriodicalCardEditor);

window.customCards = window.customCards ?? [];
window.customCards.push({
  type:        'periodical-card',
  name:        'Periodical',
  description: 'Work schedule, shifts, pay, vacation and year overview — auto-discovered from the Periodical integration.',
  preview:     true,
  editor:      'periodical-card-editor',
});
