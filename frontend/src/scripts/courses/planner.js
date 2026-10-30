// OBU pre-registration worksheet builder. All untrusted text (catalog, schedule, files)
// is inserted with textContent, never as HTML.
import * as api from './api.js';
import * as P from './plan.js';
import { initBridge, publishPlan, requestDownload } from './bridge.js';
import { DAY_LABELS, DAY_ORDER, PALETTE, clashes, clock, parseDays, parseTime, reviewSelections, week } from './schedule.js';

const $ = (sel, root = document) => root.querySelector(sel);
const root = $('#planner');
const HOUR_PX = 50; // a 50-minute class (~42px) fits both block lines
const S = {
  plan: null,
  indexYear: null, // catalog year S.index was loaded for
  index: new Map(), // code -> {code, title, hours_min, hours_max, subject, subject_name}
  details: new Map(), // `${year}|${code}` -> full catalog entry, or a pending Promise
  catalogs: null,
  offerings: null,
  refreshing: false,
  check: null,
  info: new Map(), // `${term}|${code}` -> {status, warnings} from the last check
  undo: [],
  checkTimer: 0,
  checkAbort: null,
  open: null, // expanded course in Find classes
  descOpen: new Set(), // courses whose description is fully expanded
};

// Embedding: the parent origin once the handshake succeeds (null standalone). Every save mirrors the
// plan to the parent's localStorage so on-site and standalone visits share one plan.
let bridgeOrigin = null;
let parentDownloads = false; // the embedding loader saves files for us (see download())
function persist() {
  P.savePlan(S.plan);
  publishPlan(bridgeOrigin, S.plan);
}

/** Tiny DOM builder: h('div', {class: 'x', onclick}, 'text', child). */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function status(message, { tone = 'info', undoable = false } = {}) {
  const el = $('#status');
  el.dataset.tone = tone;
  el.replaceChildren(message || '');
  if (undoable && S.undo.length) el.append(' ', h('button', { type: 'button', class: 'link', onclick: undo }, 'Undo'));
}

const fmt = (n) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
const termName = (id) => (id === 'TR' ? 'Transfer / AP' : P.termLabel(id));

// ------------------------------------------------------------------ plan helpers

const focus = () => S.plan.focus_term;
const focusTerm = () => S.plan.terms.find((t) => t.id === focus());
const picks = () => focusTerm()?.courses ?? [];
const semester = () => S.offerings?.semesters.find((s) => s.term_id === focus()) ?? null;
const sectionsFor = (code, termId = focus()) => (S.offerings?.courses?.[code] ?? []).filter((r) => r.term_id === termId);
const sectionOf = (entry) => (entry.seq ? sectionsFor(entry.code).find((s) => s.seq === entry.seq) : null);
const scheduleHours = (entry) => sectionOf(entry)?.hours ?? S.offerings?.courses?.[entry.code]?.find((r) => typeof r.hours === 'number')?.hours;
// OBU numbers end in the credit hours (CSCI 2113 = 3); the server applies the same rule.
const numberHours = (code) => (/^[A-Z]+ \d{4}$/.test(code) ? Number(code.at(-1)) : 0);
const hoursOf = (entry) =>
  typeof entry.hours === 'number' ? entry.hours : sectionOf(entry)?.hours ?? S.index.get(entry.code)?.hours_min ?? scheduleHours(entry) ?? numberHours(entry.code);
const titleOf = (code) => S.index.get(code)?.title ?? (S.offerings?.courses?.[code]?.[0]?.title || 'Not in this catalog');
const entryTitle = (entry) => (entry.custom && entry.title) || titleOf(entry.code);
const dayText = (days) => (parseDays(days) ?? []).map((d) => (d === 'TH' ? 'Th' : d)).join('');
/** One format everywhere: "MWF 1:00p–1:50p" (OBU's raw text if it isn't a regular meeting, e.g. TBA). */
const meetingText = (days, time) => {
  const span = parseTime(time);
  const d = dayText(days);
  return span && d ? `${d} ${clock(span[0])}–${clock(span[1])}` : [days, time].filter(Boolean).join(' ');
};
/** Rate My Professors search for an OBU instructor (school 745); the schedule lists names as "Last, First". */
const RMP_SCHOOL = 745;
function rmpLink(instructor) {
  const [last, first] = String(instructor ?? '').split(',').map((s) => s.trim());
  if (!last || !first) return null; // blank or STAFF
  const name = `${first} ${last}`;
  return h(
    'a',
    {
      class: 'rmp',
      href: `https://www.ratemyprofessors.com/search/professors/${RMP_SCHOOL}?q=${encodeURIComponent(name)}`,
      target: '_blank',
      rel: 'noopener noreferrer',
      title: `${name} on Rate My Professors`,
      'aria-label': `${name} on Rate My Professors (opens in a new tab)`,
    },
    '↗',
  );
}
// Courses missing from the catalog (special topics, new courses) take their credit hours from the schedule.
const apiPlan = () => {
  const body = P.forApi(S.plan);
  const fill = (c) => {
    if (typeof c.hours === 'number' || S.index.get(c.code)) return c;
    const hours = scheduleHours(c);
    return typeof hours === 'number' ? { ...c, hours } : c;
  };
  return { ...body, terms: body.terms.map((t) => ({ ...t, courses: t.courses.map(fill) })) };
};

function ensureFocusTerm(plan) {
  if (plan.focus_term && !plan.terms.some((t) => t.id === plan.focus_term)) {
    plan.terms.push({ id: plan.focus_term, courses: [] });
    plan.terms.sort((a, b) => P.termKey(a.id) - P.termKey(b.id));
  }
}

function commit(next) {
  S.undo.push(JSON.stringify(S.plan));
  if (S.undo.length > 40) S.undo.shift();
  ensureFocusTerm(next);
  S.plan = next;
  persist();
  renderAll();
  scheduleCheck();
}

function mutate(fn) {
  const next = structuredClone(S.plan);
  fn(next);
  commit(next);
}

function undo() {
  const prev = S.undo.pop();
  if (!prev) return;
  S.plan = P.cleanPlan(JSON.parse(prev));
  persist();
  renderAll();
  scheduleCheck();
  status('Undone.');
}

function addSection(code, seq) {
  const existing = picks().find((c) => c.code === code);
  mutate((plan) => {
    const list = plan.terms.find((t) => t.id === plan.focus_term).courses;
    const hit = list.find((c) => c.code === code);
    if (hit) hit.seq = seq;
    else list.push(seq ? { code, seq } : { code });
  });
  status(existing ? `Switched ${code} to section ${seq}.` : `Added ${code}.`, { undoable: true });
}

function addCourse(code) {
  const sections = sectionsFor(code);
  if (sections.length === 1) return addSection(code, sections[0].seq);
  S.open = code;
  $('#q').value = code;
  showTab('find');
  renderFind();
}

function removePick(code) {
  mutate((plan) => {
    const list = plan.terms.find((t) => t.id === plan.focus_term).courses;
    list.splice(list.findIndex((c) => c.code === code), 1);
  });
  status(`Removed ${code}.`, { undoable: true });
}

// ------------------------------------------------------------------ checks

function scheduleCheck() {
  clearTimeout(S.checkTimer);
  S.checkTimer = setTimeout(runCheck, 300);
}

async function runCheck() {
  if (!S.indexYear) return;
  S.checkAbort?.abort();
  const abort = (S.checkAbort = new AbortController());
  try {
    S.check = await api.checkPlan(apiPlan(), abort.signal);
    S.info = new Map();
    for (const term of S.check.terms) for (const c of term.courses) S.info.set(`${term.id}|${c.code}`, c);
    renderSheet();
    renderMajor();
    renderFind();
  } catch (err) {
    if (err.name !== 'AbortError') status(`Couldn't check your picks right now (${err.message}).`, { tone: 'error' });
  }
}

function problems(entry) {
  const info = S.info.get(`${focus()}|${entry.code}`);
  // Custom classes are the student's own entry: "not offered/scheduled" warnings don't apply.
  const onSchedule = entry.custom || sectionsFor(entry.code).length > 0;
  return (info?.warnings ?? []).filter(
    (w) => (w.severity === 'error' || w.severity === 'warn') && !(onSchedule && (w.kind === 'offered' || w.kind === 'schedule')),
  );
}

/** Load the latest cached listing (tab return/focus) and recheck the schedule against it. */
async function refreshSchedule() {
  if (S.refreshing || root.dataset.state !== 'ready') return;
  S.refreshing = true;
  try {
    S.offerings = await api.getOfferings();
    renderAll();
    scheduleCheck();
  } catch (err) {
    status(`Couldn't refresh the schedule (${err.message}). Still showing the previous cached listing.`, { tone: 'error' });
  } finally {
    S.refreshing = false;
  }
}

// ------------------------------------------------------------------ worksheet

function colorFor(i) {
  return PALETTE[i % PALETTE.length];
}

function renderSheet() {
  const sem = semester();
  $('#sheet-title').textContent = sem?.label ?? (focus() ? termName(focus()) : 'Your schedule');
  const list = picks();
  const review = reviewSelections(list, focus(), S.offerings);
  const table = $('#sheet-table');
  table.replaceChildren();
  const items = list.map((entry, i) => {
    if (entry.custom) {
      const span = entry.days ? [entry.start, entry.end] : null;
      return { key: entry.code, label: entry.code, title: entryTitle(entry), days: entry.days, span, color: colorFor(i) };
    }
    const s = sectionOf(entry);
    return { key: entry.code, label: entry.code, title: titleOf(entry.code), days: s?.days, time: s?.time, room: s?.room, color: colorFor(i) };
  });
  const wk = week(items);
  const pairs = conflictPairs(wk.conflicts);
  const conflictCodes = new Set(pairs.flatMap((c) => [c.a, c.b]));
  const total = list.reduce((sum, e) => sum + hoursOf(e), 0);

  table.append(
    list.length
      ? h(
          'table',
          { class: 'sheet-table' },
          h('thead', {}, h('tr', {}, ['', 'Course', 'Section', 'When', 'Instructor', 'Hours', ''].map((t) => h('th', { scope: 'col' }, t)))),
          h('tbody', {}, list.map((entry, i) => sheetRow(entry, i, conflictCodes.has(entry.code), pairs, review.issues.find((w) => w.code === entry.code)))),
          h('tfoot', {}, h('tr', {}, h('td', { colspan: 5 }, 'Total credit hours'), h('td', {}, h('b', {}, fmt(total))),
            h('td', {}, h('button', { type: 'button', class: 'icon', 'aria-label': 'Copy schedule', title: 'Copy schedule', onclick: copySchedule }, '⧉')))),
        )
      : h('p', { class: 'sheet-empty' }, 'No classes yet. Add them from Find classes or My major, or add a custom class.'),
  );
  const termWarn = (S.check?.terms.find((t) => t.id === focus())?.warnings ?? []).map((w) => w.message);
  $('#sheet-total').replaceChildren(
    ...[
      list.length ? `${fmt(total)} credit hours` : '',
      termWarn.length ? h('span', { class: 'warn' }, ` · ${termWarn.join(' ')}`) : null,
    ].filter(Boolean),
  );
  const ready = !!sem && list.some((e) => e.custom || sectionOf(e));
  $('#print').disabled = !ready;
  $('#download').disabled = !ready;
  renderValidation(list, review, pairs, wk);
  renderCalendar(wk);
}

/** Copy the focused schedule as tab-separated rows (pastes into a spreadsheet, notes, or email). */
async function copySchedule() {
  const rows = picks().map((entry) => {
    const s = entry.custom ? null : sectionOf(entry);
    const when = entry.custom
      ? entry.days ? `${dayText(entry.days)} ${clock(entry.start)}–${clock(entry.end)}` : 'TBA'
      : s ? meetingText(s.days, s.time) : '';
    const section = entry.custom ? 'Custom' : s ? s.seq : entry.seq ? `${entry.seq} (no longer listed)` : '';
    return [section, entry.code, entryTitle(entry), when, s?.instructor ?? '', fmt(hoursOf(entry))];
  });
  const text = [['Section', 'Course', 'Title', 'When', 'Instructor', 'Hours'], ...rows].map((r) => r.join('\t')).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    status('Schedule copied.');
  } catch {
    status("Couldn't copy to the clipboard in this browser.", { tone: 'error' });
  }
}

/** Problems-only banner: nothing shows when the schedule checks out. */
function renderValidation(list, review, pairs, wk) {
  const box = $('#schedule-validation');
  const unavailable = review.issues.filter((w) => w.severity === 'error').length;
  const unselected = review.issues.filter((w) => w.kind === 'unselected').length;
  const unknownTimes = wk.unscheduled.filter((item) => {
    const entry = list.find((e) => e.code === item.key);
    return entry.custom || sectionOf(entry);
  }).length;
  const unchecked = !review.semester && list.some((e) => !e.custom);
  const invalid = unavailable > 0 || pairs.length > 0;
  const incomplete = unselected > 0 || unknownTimes > 0;
  box.dataset.state = invalid ? 'invalid' : unchecked ? 'unchecked' : incomplete ? 'incomplete' : 'valid';
  const details = [];
  if (unavailable) details.push(`${unavailable} unavailable class selection${unavailable === 1 ? '' : 's'}`);
  if (pairs.length) details.push(`${pairs.length} time conflict${pairs.length === 1 ? '' : 's'}`);
  if (unselected) details.push(`${unselected} class${unselected === 1 ? ' needs' : 'es need'} a section`);
  if (unknownTimes) details.push(`${unknownTimes} meeting${unknownTimes === 1 ? ' has' : 's have'} no checkable day/time`);
  if (unchecked) details.push(`No cached listing for ${termName(focus())}; saved sections cannot be validated`);
  box.hidden = !details.length;
  if (!details.length) return box.replaceChildren();
  box.replaceChildren(
    h('strong', { class: invalid ? 'bad' : 'warn' },
      invalid ? 'Schedule needs changes.' : unchecked ? 'Schedule not validated.' : 'Schedule needs review.'),
    h('p', {}, `${details.join(' · ')}. Your saved choices are kept. Review the rows below.${unknownTimes || unavailable || unchecked ? ' Overlaps cannot be fully checked without listed meeting times.' : ''}`),
  );
}

/** One entry per overlapping pair, with the days it happens ("Overlaps CORE 2243 on Mon, Wed, Fri"). */
function conflictPairs(conflicts) {
  const byPair = new Map();
  for (const c of conflicts) {
    const [a, b] = [c.a?.label ?? 'Chapel', c.b?.label ?? 'Chapel'];
    const key = `${a}|${b}`;
    if (!byPair.has(key)) byPair.set(key, { a, b, days: [], from: c.from, to: c.to });
    byPair.get(key).days.push(DAY_LABELS[c.day].slice(0, 3));
  }
  return [...byPair.values()];
}

function sheetRow(entry, i, conflict, conflicts, listingIssue) {
  const sections = entry.custom ? [] : sectionsFor(entry.code);
  const s = entry.custom ? null : sectionOf(entry);
  const issues = problems(entry);
  if (entry.custom) {
    if (!entry.days) issues.unshift({ severity: 'info', message: 'No meeting time set, so it is not on the calendar.' });
  } else if (listingIssue) issues.unshift(listingIssue);
  for (const c of conflicts) {
    if (c.a !== entry.code && c.b !== entry.code) continue;
    const other = c.a === entry.code ? c.b : c.a;
    issues.push({ severity: 'error', message: `Overlaps ${other} on ${c.days.join(', ')} (${clock(c.from)}–${clock(c.to)}).` });
  }
  const [fill, ink] = colorFor(i);
  const select = entry.custom
    ? h('button', { type: 'button', class: 'link', onclick: () => customDialog(entry) }, 'Custom · Edit')
    : sections.length
      ? h(
          'select',
          {
            'aria-label': `Section for ${entry.code}`,
            onchange: (e) => addSection(entry.code, e.target.value),
          },
          s ? null : entry.seq
            ? h('option', { value: entry.seq, selected: true, disabled: true }, `${entry.seq} · unavailable`)
            : h('option', { value: '' }, 'Choose…'),
          sections.map((x) => h('option', { value: x.seq, selected: x.seq === entry.seq }, `${x.seq} · ${meetingText(x.days, x.time)}`)),
        )
      : h('span', { class: listingIssue?.severity === 'error' ? 'bad' : 'muted' }, entry.seq ? `${entry.seq} · ${semester() ? 'unavailable' : 'not verified'}` : '—');
  const when = entry.custom ? (entry.days ? `${dayText(entry.days)} ${clock(entry.start)}–${clock(entry.end)}` : 'TBA') : s ? meetingText(s.days, s.time) : '';
  const icon = { error: '⛔', warn: '⚠', info: 'ℹ' };
  return [
    h(
      'tr',
      { class: `${conflict ? 'conflict' : ''}` },
      h('td', {}, h('span', { class: 'swatch', style: `background:${fill};border-color:${ink}` })),
      h('td', {}, h('b', {}, entry.code), h('div', { class: 'muted small' }, entryTitle(entry))),
      h('td', {}, select),
      h('td', {}, when, s?.room ? h('div', { class: 'muted small' }, s.room) : null),
      h('td', {}, s?.instructor || '', rmpLink(s?.instructor), s && s.seats != null ? h('div', { class: `small ${s.seats > 0 ? 'muted' : 'bad'}` }, `${s.seats} seats`) : null),
      h('td', {}, fmt(hoursOf(entry))),
      h('td', {}, h('button', { type: 'button', class: 'icon', 'aria-label': `Remove ${entry.code}`, onclick: () => removePick(entry.code) }, '✕')),
    ),
    issues.length
      ? h('tr', { class: 'issues' }, h('td', {}), h('td', { colspan: 6 }, issues.map((w) => h('div', { class: w.severity === 'error' ? 'bad' : w.severity === 'info' ? 'muted' : 'warn' }, `${icon[w.severity] ?? '⚠'} ${w.message}`))))
      : null,
  ];
}

function renderCalendar(wk) {
  const cal = $('#calendar');
  cal.replaceChildren();
  const height = (wk.last - wk.first) * HOUR_PX;
  const hours = h('div', { class: 'cal-hours' }, h('div', { class: 'cal-head' }), h('div', { class: 'cal-body', style: `height:${height}px` },
    Array.from({ length: wk.last - wk.first }, (_, i) => h('div', { class: 'cal-hour', style: `top:${i * HOUR_PX}px` }, clock((wk.first + i) * 60)))));
  const days = wk.days.map((d) =>
    h(
      'div',
      { class: 'cal-day' },
      h('div', { class: 'cal-head' }, DAY_LABELS[d].slice(0, 3)),
      h(
        'div',
        { class: 'cal-body', style: `height:${height}px` },
        Array.from({ length: wk.last - wk.first }, (_, i) => h('div', { class: 'cal-line', style: `top:${i * HOUR_PX}px` })),
        wk.byDay[d].map((e) => {
          const top = ((e.start - wk.first * 60) / 60) * HOUR_PX;
          // Code + time on the first line; the title gets as many whole lines as the block has room for
          // (one for a 50-minute class, more for longer ones) and ends in "…" past that. The full title
          // is in the hover tooltip and the table above. Side-by-side conflicts show the code only.
          const size = Math.max(((e.end - e.start) / 60) * HOUR_PX, e.item && e.cols === 1 ? 40 : 24);
          const titleLines = Math.max(1, Math.floor((size - 7 - 16.4) / 14.8)); // px: padding+border, code line, title line
          const width = 100 / e.cols;
          const style = `top:${top}px;height:${size}px;left:calc(${e.col * width}% + 2px);width:calc(${width}% - 4px);`;
          if (!e.item) return h('div', { class: `ev chapel ${e.conflict ? 'conflict' : ''}`, style }, h('b', {}, 'Chapel'));
          const [fill, ink] = e.item.color;
          const time = `${clock(e.start)}–${clock(e.end)}`;
          return h(
            'div',
            { class: `ev ${e.cols > 1 ? 'narrow' : ''} ${e.conflict ? 'conflict' : ''}`, style: `${style}background:${fill};border-color:${ink};color:${ink}`, title: `${e.item.label} · ${e.item.title} · ${time}` },
            h('div', { class: 'ev-top' }, h('b', {}, e.item.label), e.cols === 1 ? h('span', { class: 'ev-time' }, time) : null),
            e.cols === 1 ? h('span', { class: 'ev-title', style: `--lines:${titleLines}` }, e.item.title) : null,
          );
        }),
      ),
    ),
  );
  cal.append(h('div', { class: 'cal-grid', style: `--days:${wk.days.length}` }, hours, days));
  if (wk.unscheduled.length) cal.append(h('p', { class: 'muted small' }, `Not on the calendar (online, TBA, or no section picked): ${wk.unscheduled.map((u) => u.label).join(', ')}`));
}

// ------------------------------------------------------------------ find classes

function offeredCodes() {
  const out = [];
  for (const [code, rows] of Object.entries(S.offerings?.courses ?? {})) if (rows.some((r) => r.term_id === focus())) out.push(code);
  return out;
}

function neededCodes() {
  const out = new Set();
  for (const p of S.check?.programs ?? []) {
    for (const l of leaves(p.requirements)) {
      if (l.status !== 'missing') continue;
      if (l.kind === 'course') out.add(l.node.course);
      if (l.kind === 'choose') for (const i of l.node.items) if (i.type === 'course') out.add(i.course);
    }
  }
  return out;
}

function renderFind() {
  const list = $('#results');
  list.replaceChildren();
  if (!S.offerings) {
    list.append(h('li', { class: 'empty' }, 'Loading the class schedule…'));
    return;
  }
  const q = $('#q').value.trim().toLowerCase();
  const subject = $('#subject').value;
  const tokens = q.split(/\s+/).filter(Boolean);
  const codeQuery = P.normalizeCode(q);
  const needed = neededCodes();
  const picked = new Set(picks().map((c) => c.code));
  // Filter checkboxes. "In my programs" needs a checked major/core to mean anything.
  const programBox = $('#f-program');
  programBox.disabled = !needed.size;
  const byProgram = programBox.checked && needed.size > 0;
  const byFit = $('#f-fits').checked;
  let codes = offeredCodes().filter((c) => !subject || c.startsWith(`${subject} `));
  if (!tokens.length) codes = codes.filter((c) => !picked.has(c)); // browsing: what's already added is on the worksheet
  if (tokens.length) {
    codes = codes
      .map((code) => {
        if (code === codeQuery) return [-100, code];
        const rows = sectionsFor(code);
        const hay = `${code} ${titleOf(code)} ${rows.map((r) => `${r.title} ${r.instructor}`).join(' ')}`.toLowerCase();
        if (!tokens.every((t) => hay.includes(t))) return null;
        return [-tokens.filter((t) => `${code} ${titleOf(code)}`.toLowerCase().includes(t)).length, code];
      })
      .filter(Boolean)
      .sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]))
      .map((x) => x[1]);
  } else codes.sort();
  const matched = codes.length;
  // Picked courses ("✓ added") and an exact course-code search always show, whatever the filters say.
  codes = codes.filter((c) => picked.has(c) || c === codeQuery || ((!byProgram || needed.has(c)) && (!byFit || fits(c))));
  const hidden = matched - codes.length;
  const sem = semester()?.label ?? 'this semester';
  list.append(
    h(
      'li',
      { class: 'list-note' },
      codes.length
        ? `${codes.length} ${sem} course${codes.length === 1 ? '' : 's'}${hidden ? ` · ${hidden} hidden by filters` : ''}`
        : matched
          ? `All ${matched} matching courses are hidden by the filters above.`
          : tokens.length
            ? `No ${sem} classes match.`
            : needed.size || !byProgram
              ? `Nothing else offered ${sem}. Search above for electives.`
              : 'Search above, or choose your major to see its courses.',
    ),
  );
  for (const code of codes.slice(0, 80)) list.append(resultItem(code, picked.has(code)));
  if (codes.length > 80) list.append(h('li', { class: 'empty' }, `Showing 80 of ${codes.length}. Search or pick a subject to narrow it down.`));
}

/** Meetings already on the worksheet (sections and timed custom classes), except `code` itself. */
function chosenMeetings(code) {
  return picks()
    .filter((c) => c.code !== code)
    .map((c) => (c.custom ? { days: c.days, span: c.days ? [c.start, c.end] : null, label: c.code } : { ...sectionOf(c), label: c.code }))
    .filter((s) => s.days);
}

/** Does any section of `code` avoid every picked class and chapel? (Online/TBA sections always fit.) */
function fits(code) {
  const chosen = chosenMeetings(code);
  return sectionsFor(code).some((s) => !clashes(s, chosen).length);
}

/** Full catalog entry for one course, fetched the first time it's opened; null while loading or if absent. */
function courseDetail(code) {
  const key = `${S.indexYear}|${code}`;
  const hit = S.details.get(key);
  if (hit && !(hit instanceof Promise)) return hit;
  if (!hit && S.index.has(code)) {
    const load = api
      .getCourse(S.indexYear, code)
      .catch(() => ({ missing: true }))
      .then((course) => {
        S.details.set(key, course);
        if (S.open === code) renderFind();
      });
    S.details.set(key, load);
  }
  return null;
}

function resultItem(code, picked) {
  const open = S.open === code;
  const course = open ? courseDetail(code) : null;
  const loadingDesc = open && !course && S.index.has(code);
  const rows = sectionsFor(code);
  const chosen = chosenMeetings(code);
  return h(
    'li',
    { class: `result ${open ? 'open' : ''}` },
    h(
      'button',
      {
        type: 'button',
        class: 'result-head',
        'aria-expanded': String(open),
        onclick: () => {
          S.open = open ? null : code;
          renderFind();
        },
      },
      h('span', { class: 'code' }, code),
      h('span', { class: 'title' }, titleOf(code)),
      h('span', { class: 'meta' }, picked ? '✓ added' : `${rows.length} section${rows.length > 1 ? 's' : ''}`),
    ),
    open
      ? h(
          'div',
          { class: 'result-body' },
          loadingDesc ? h('p', { class: 'muted small' }, 'Loading description…') : null,
          course?.description
            ? [
                h('p', { class: `desc ${S.descOpen.has(code) ? 'full' : ''}` }, course.description),
                course.description.length > 160 ? h('button', { type: 'button', class: 'link read-all', onclick: () => (S.descOpen.has(code) ? S.descOpen.delete(code) : S.descOpen.add(code), renderFind()) }, S.descOpen.has(code) ? 'Show less' : 'Read all') : null,
              ]
            : null,
          h(
            'ul',
            { class: 'sections' },
            rows.map((s) => {
              const clash = clashes(s, chosen);
              const mine = picks().some((c) => c.code === code && c.seq === s.seq);
              return h(
                'li',
                {},
                h('div', {}, h('b', {}, meetingText(s.days, s.time)), ` · ${s.instructor || 'Staff'}`, rmpLink(s.instructor), h('div', { class: 'muted small' }, `Section ${s.seq}${s.room ? ` · ${s.room}` : ''} · `, h('span', { class: s.seats > 0 ? '' : 'bad' }, `${s.seats ?? '?'} seats`))),
                clash.length ? h('div', { class: 'bad small' }, `Overlaps ${clash.join(', ')}`) : null,
                mine
                  ? h('span', { class: 'muted small' }, 'Added')
                  : h('button', { type: 'button', class: clash.length ? '' : 'primary', onclick: () => addSection(code, s.seq) }, picked ? 'Switch' : 'Add'),
              );
            }),
          ),
        )
      : null,
  );
}

// ------------------------------------------------------------------ my major

/** Flatten a requirement tree into checklist items. */
function leaves(nodes, out = []) {
  for (const n of nodes ?? []) {
    if (n.chosen === false) continue;
    if (n.type === 'choose' && n.status === 'missing') out.push({ kind: 'choose', status: 'missing', node: n });
    else if (n.type === 'all' || n.type === 'choose') leaves(n.items, out);
    else out.push({ kind: n.type, status: n.status, node: n });
  }
  return out;
}

function addButton(code) {
  const offered = sectionsFor(code).length > 0;
  const inSheet = picks().some((c) => c.code === code);
  if (inSheet) return h('span', { class: 'tag-ok' }, 'on worksheet');
  if (!offered) return h('span', { class: 'muted small' }, `not offered ${semester()?.label ?? ''}`);
  return h('button', { type: 'button', class: 'add', onclick: () => addCourse(code) }, 'Add');
}

function leafItem(l) {
  const n = l.node;
  if (l.kind === 'course') {
    const where = n.courses?.[0];
    return h('li', { class: l.status }, h('span', {}, h('b', {}, n.course), ' ', titleOf(n.course), where && where.term !== focus() ? h('span', { class: 'muted' }, ` · ${termName(where.term)}`) : null), l.status === 'missing' ? addButton(n.course) : null);
  }
  if (l.kind === 'hours') {
    const left = Math.max(0, n.hours_required - n.hours_planned);
    return h(
      'li',
      { class: l.status },
      h('span', {}, `${n.hours_required} hours of `, h('b', {}, n.description), n.label ? h('span', { class: 'muted' }, ` (${n.label})`) : null, n.message ? h('div', { class: 'muted small' }, n.message) : null),
      l.status === 'missing'
        ? h('button', { type: 'button', class: 'add', onclick: () => browse(n.from) }, 'Browse')
        : null,
    );
  }
  if (l.kind === 'choose') {
    // Labels often already say "(Choose one course)"; only add our own "Choose N" when they don't.
    const label = n.label?.trim();
    const head = label && /\bchoose\b/i.test(label) ? label : `Choose ${n.count ?? 1}${label ? ` · ${label}` : ''}`;
    return h(
      'li',
      { class: 'missing choose' },
      h('span', { class: 'choose-head' }, `${head}:`),
      h(
        'ul',
        { class: 'options' },
        n.items.map((i) =>
          i.type === 'course'
            ? h('li', { class: 'opt' }, h('span', {}, h('b', {}, i.course), ' ', titleOf(i.course)), addButton(i.course))
            : h('li', { class: 'opt muted' }, i.label ?? i.type),
        ),
      ),
    );
  }
  return h('li', { class: 'manual' }, h('span', {}, n.label ? h('b', {}, `${n.label}: `) : null, n.text));
}

function renderMajor() {
  const el = $('#checklist');
  el.replaceChildren();
  if (!S.check) return;
  const programs = S.check.programs.filter((p) => p.status !== 'unknown');
  if (!programs.some((p) => p.kind === 'major')) el.append(h('p', {}, h('button', { type: 'button', class: 'primary', onclick: () => programsDialog() }, 'Choose your major')));
  const order = { major: 0, minor: 1, core: 2 };
  for (const p of programs.sort((a, b) => order[a.kind] - order[b.kind])) {
    const items = leaves(p.requirements);
    // Requirements the student can act on this semester come first.
    const actionable = (l) =>
      l.kind === 'hours' ||
      (l.kind === 'course' && sectionsFor(l.node.course).length > 0) ||
      (l.kind === 'choose' && l.node.items.some((i) => i.type === 'course' && sectionsFor(i.course).length > 0));
    const missing = items.filter((l) => l.status === 'missing').sort((a, b) => actionable(b) - actionable(a));
    const manual = items.filter((l) => l.status === 'manual');
    const covered = items.filter((l) => l.status === 'done' || l.status === 'planned');
    el.append(
      h(
        'section',
        { class: 'prog' },
        h('h3', {}, p.kind === 'core' ? 'Core curriculum' : `${p.name}${p.degree ? `, ${p.degree}` : ''}${p.emphasis ? ` (${p.emphasis})` : ''}`),
        h('p', { class: 'muted small' }, 'Catalog requirements for this program'),
        missing.length ? h('ul', { class: 'need' }, missing.map(leafItem)) : null,
        manual.length ? h('details', {}, h('summary', {}, `Other requirements (${manual.length})`), h('ul', { class: 'need' }, manual.map(leafItem))) : null,
        covered.length ? h('details', {}, h('summary', {}, `Covered (${covered.length})`), h('ul', { class: 'need' }, covered.map(leafItem))) : null,
      ),
    );
  }
  const g = S.check.graduation;
  el.append(h('p', { class: 'muted small grad' }, `Toward graduation: ${fmt(g.total_hours.planned)} of ${g.total_hours.required} hours, including this semester.`));
}

function browse(from) {
  const subjects = from?.subjects ?? [];
  $('#subject').value = subjects[0] ?? '';
  $('#q').value = !subjects.length && from?.courses?.length ? from.courses[0] : '';
  $('#f-program').checked = false; // elective pools reach beyond the listed requirement courses
  if (subjects.length > 1) status(`Showing ${subjects[0]}; this also accepts ${subjects.slice(1).join(', ')}.`);
  showTab('find');
  renderFind();
}

function showTab(tab) {
  for (const b of root.querySelectorAll('[data-tab]')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  $('#pane-find').hidden = tab !== 'find';
  $('#pane-req').hidden = tab !== 'req';
}

// ------------------------------------------------------------------ dialogs

const dlg = $('#dlg');

function openDialog(title, body, actions) {
  $('#dlg-title').textContent = title;
  $('#dlg-body').replaceChildren(...[body].flat().filter(Boolean));
  $('#dlg-actions').replaceChildren(
    ...actions.map(([label, fn, cls]) =>
      h(
        'button',
        {
          type: fn ? 'button' : 'submit',
          class: cls || '',
          onclick: fn
            ? async () => {
                if ((await fn()) !== false) dlg.close();
              }
            : null,
        },
        label,
      ),
    ),
  );
  if (!dlg.open) dlg.showModal();
}

function programName(id) {
  const find = (list) => list?.find?.((x) => x.id === id);
  const years = S.catalogs?.years ?? [];
  const p = find(years.find((y) => y.catalog_year === S.plan.catalog_year)?.programs) ?? find(years.flatMap((y) => y.programs));
  return p ? `${p.name}${p.degree ? `, ${p.degree}` : ''}${p.emphasis ? ` (${p.emphasis})` : ''}` : id;
}

function renderPill() {
  const names = S.plan.programs.filter((id) => id !== 'core').map(programName);
  $('#program-pill').textContent = names.length ? `${names.join(' + ')} ▾` : 'Choose your major ▾';
}

const MAX_PER_KIND = 3;

/**
 * Majors and minors: up to three of each, picked through a searchable list.
 * The selection is kept in `chosen` until Save.
 */
function programsDialog(firstRun = false) {
  const years = S.catalogs.years;
  const yearSel = h('select', {}, years.map((y) => h('option', { value: y.catalog_year, selected: y.catalog_year === S.plan.catalog_year }, y.catalog_year)));
  const chosen = S.plan.programs.filter((id) => id !== 'core');
  const label = (p) => `${p.name}${p.degree ? `, ${p.degree}` : ''}${p.emphasis ? ` (${p.emphasis})` : ''}`;
  const yearPrograms = () => years.find((y) => y.catalog_year === yearSel.value)?.programs ?? [];
  const sections = { major: h('div', { class: 'pick' }), minor: h('div', { class: 'pick' }) };
  let searching = firstRun && !chosen.some((id) => id.startsWith('major-')) ? 'major' : null;

  const draw = () => {
    const programs = yearPrograms();
    const byId = new Map(programs.map((p) => [p.id, p]));
    for (const kind of ['major', 'minor']) {
      const mine = chosen.filter((id) => byId.get(id)?.kind === kind);
      const box = sections[kind];
      const chips = h(
        'ul',
        { class: 'chips' },
        mine.map((id) =>
          h('li', {}, label(byId.get(id)), ' ', h('button', { type: 'button', class: 'icon', 'aria-label': `Remove ${label(byId.get(id))}`, onclick: () => (chosen.splice(chosen.indexOf(id), 1), draw()) }, '✕')),
        ),
      );
      const parts = [h('h3', {}, kind === 'major' ? 'Majors' : 'Minors'), mine.length ? chips : h('p', { class: 'muted small' }, kind === 'major' ? 'No major yet.' : 'No minor yet.')];
      if (searching === kind) {
        const input = h('input', { type: 'search', placeholder: `Search ${kind}s…`, 'aria-label': `Search ${kind}s`, autocomplete: 'off' });
        const list = h('ul', { class: 'pick-list' });
        const fill = () => {
          const q = input.value.trim().toLowerCase();
          const hits = programs.filter((p) => p.kind === kind && !chosen.includes(p.id) && label(p).toLowerCase().includes(q)).sort((a, b) => label(a).localeCompare(label(b)));
          list.replaceChildren(
            ...hits.map((p) =>
              h('li', {}, h('button', { type: 'button', onclick: () => { chosen.push(p.id); searching = null; draw(); } }, label(p))),
            ),
            ...(hits.length ? [] : [h('li', { class: 'muted small' }, 'No matches.')]),
          );
        };
        input.addEventListener('input', fill);
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            list.querySelector('button')?.click();
          }
        });
        fill();
        parts.push(input, list);
        queueMicrotask(() => input.focus());
      } else if (mine.length < MAX_PER_KIND) {
        parts.push(h('button', { type: 'button', class: 'add-prog', onclick: () => { searching = kind; draw(); } }, kind === 'major' ? '+ Add a major' : '+ Add a minor'));
      }
      box.replaceChildren(...parts);
    }
  };
  yearSel.addEventListener('change', () => {
    const valid = new Set(yearPrograms().map((p) => p.id));
    chosen.splice(0, chosen.length, ...chosen.filter((id) => valid.has(id)));
    draw();
  });
  draw();
  openDialog(firstRun ? 'What are you studying?' : 'Your majors and minors', [
    firstRun ? h('p', {}, 'Pick your major so the planner can show what you still need.') : null,
    sections.major,
    sections.minor,
    h('label', { class: 'year-field' }, 'Catalog year ', yearSel, h('span', { class: 'muted small' }, ' usually the year you started at OBU')),
  ], [
    [firstRun ? 'Skip' : 'Cancel', null],
    [
      'Save',
      async () => {
        const ids = ['core', ...chosen];
        if (yearSel.value !== S.plan.catalog_year) return switchYear(yearSel.value, ids);
        mutate((plan) => (plan.programs = ids));
        showTab('req');
      },
      'primary',
    ],
  ]);
}

async function switchYear(year, ids) {
  const target = S.catalogs?.years?.find((y) => y.catalog_year === year);
  const valid = new Set(target?.programs.map((p) => p.id) ?? []);
  loading(30, `Loading the ${year} catalog…`);
  try {
    await loadIndex(year);
  } finally {
    loading(null);
  }
  mutate((plan) => {
    plan.catalog_year = year;
    plan.programs = ids.filter((id) => valid.has(id));
  });
  showTab('req');
}

/** Print or download the filled-in worksheet. Name and ID are only sent to render the page, never saved. */
function worksheetDialog(mode) {
  const sem = semester();
  const chosen = picks().filter((c) => !c.custom && sectionOf(c));
  const custom = picks().filter((c) => c.custom);
  const missing = picks().filter((c) => !c.seq && !c.custom).map((c) => c.code);
  const unavailable = picks().filter((c) => c.seq && !c.custom && !sectionOf(c)).map((c) => `${c.code} (section ${c.seq})`);
  const name = h('input', { maxlength: 80, autocomplete: 'name' });
  const sid = h('input', { maxlength: 20, autocomplete: 'off' });
  const build = async () =>
    api.buildWorksheet({
      semester: sem.code,
      catalog_year: S.indexYear, // the printout uses the same catalog titles as the screen
      sequences: chosen.map((c) => c.seq),
      custom: custom.map((c) => ({ code: c.code, title: entryTitle(c), hours: hoursOf(c), ...(c.days ? { days: c.days, start: c.start, end: c.end } : {}) })),
      student_name: name.value,
      student_id: sid.value,
      edit_url: await shareUrl(), // printed as a QR code; same schedule-only link as "Copy schedule link"
    });
  const run = (fn) => async () => {
    try {
      await fn(await build());
    } catch (err) {
      status(err.message, { tone: 'error' });
      return false;
    }
  };
  const print = run((res) => printHtml(res.html));
  const save = run(async (res) => {
    status('Making the PDF…');
    await downloadPdf(res.html, res.filename.replace(/\.html$/, '.pdf'));
    status('Worksheet downloaded.');
  });
  openDialog(`${mode === 'print' ? 'Print' : 'Download'} worksheet · ${sem.label}`, [
    unavailable.length ? h('p', { class: 'bad' }, `Left off (no longer listed): ${unavailable.join(', ')}`) : null,
    missing.length ? h('p', { class: 'warn' }, `Left off (no section picked): ${missing.join(', ')}`) : null,
    h('div', { class: 'fields' }, h('label', {}, 'Name (optional)', name), h('label', {}, 'Student ID (optional)', sid)),
  ], [
    ['Cancel', null],
    mode === 'print' ? ['Download instead', save] : ['Print instead', print],
    mode === 'print' ? ['Print', print, 'primary'] : ['Download', save, 'primary'],
  ]);
}

/** Add or edit a class that isn't on OBU's schedule (another campus, a lab, an arranged course). */
function customDialog(existing = null) {
  const minutes = (m) => (m == null ? '' : `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`);
  const code = h('input', { maxlength: 12, placeholder: 'MATH 1003', autocomplete: 'off', value: existing?.code ?? '' });
  const title = h('input', { maxlength: 80, placeholder: 'Course title', autocomplete: 'off', value: existing?.title ?? '' });
  const hours = h('input', { type: 'number', min: 0, max: 12, step: 0.5, inputmode: 'decimal', value: existing ? hoursOf(existing) : '' });
  const start = h('input', { type: 'time', step: 300, value: minutes(existing?.start) });
  const end = h('input', { type: 'time', step: 300, value: minutes(existing?.end) });
  const picked = new Set(parseDays(existing?.days) ?? []);
  const dayBoxes = DAY_ORDER.map((d) => {
    const box = h('input', { type: 'checkbox', value: d, checked: picked.has(d) });
    return h('label', { class: 'day-box' }, box, d === 'TH' ? 'Th' : d === 'S' ? 'Sa' : d);
  });
  const error = h('p', { class: 'bad small', role: 'alert' });
  // Prefill hours from the course number (OBU numbers end in the credit hours) until the student types their own.
  code.addEventListener('input', () => {
    const c = P.normalizeCode(code.value);
    if (!hours.dataset.touched && P.isCode(c)) hours.value = S.index.get(c)?.hours_min ?? numberHours(c);
    if (!title.dataset.touched && S.index.get(c)) title.value = S.index.get(c).title;
  });
  hours.addEventListener('input', () => (hours.dataset.touched = '1'));
  title.addEventListener('input', () => (title.dataset.touched = '1'));
  const toMin = (v) => (v ? Number(v.slice(0, 2)) * 60 + Number(v.slice(3, 5)) : null);
  const save = () => {
    const c = P.normalizeCode(code.value);
    const days = dayBoxes.map((l) => l.firstChild).filter((b) => b.checked).map((b) => b.value).join('');
    const [s, e] = [toMin(start.value), toMin(end.value)];
    const n = hours.value === '' ? null : Number(hours.value);
    const fail = (msg) => {
      error.textContent = msg;
      return false;
    };
    if (!P.isCode(c)) return fail('Enter a course code like MATH 1003.');
    if (c !== existing?.code && picks().some((p) => p.code === c)) return fail(`${c} is already on your worksheet.`);
    if (n != null && !(n >= 0 && n <= 12)) return fail('Credit hours must be between 0 and 12.');
    const timed = days || s != null || e != null;
    if (timed && (!days || s == null || e == null)) return fail('For a meeting time, pick the days and both start and end times (or leave all three empty).');
    if (timed && !(e > s && e - s <= 360)) return fail('The end time must be after the start time (at most 6 hours).');
    const entry = { code: c, custom: true, ...(title.value.trim() ? { title: title.value.trim().slice(0, 80) } : {}), ...(n != null ? { hours: n } : {}), ...(timed ? { days, start: s, end: e } : {}) };
    mutate((plan) => {
      const list = plan.terms.find((t) => t.id === plan.focus_term).courses;
      const at = existing ? list.findIndex((p) => p.code === existing.code) : -1;
      if (at >= 0) list[at] = entry;
      else list.push(entry);
    });
    status(existing ? `Updated ${c}.` : `Added custom class ${c}.`, { undoable: true });
  };
  openDialog(existing ? `Edit ${existing.code}` : 'Add a custom class', [
    h('p', { class: 'muted small' }, "For a class that isn't on OBU's schedule here, like an arranged course or one taken elsewhere."),
    h(
      'div',
      { class: 'fields' },
      h('label', {}, 'Course code', code),
      h('label', {}, 'Title (optional)', title),
      h('label', {}, 'Credit hours', hours),
    ),
    h('fieldset', { class: 'days' }, h('legend', {}, 'Meets (optional)'), h('div', { class: 'day-row' }, dayBoxes)),
    h('div', { class: 'fields two' }, h('label', {}, 'Starts', start), h('label', {}, 'Ends', end)),
    error,
  ], [
    ['Cancel', null],
    [existing ? 'Save' : 'Add class', save, 'primary'],
  ]);
  code.focus();
}

// The worksheet's printable width: letter minus 0.4in side margins, plus its 16px screen padding.
const SHEET_PX = 7.7 * 96 + 32;
const PAGE = { width: 8.5, height: 11, side: 0.4, top: 0.5 }; // inches; matches the worksheet's @page

/**
 * Load a self-contained HTML page into a hidden same-origin frame at the worksheet's print width.
 * srcdoc, not a blob: URL: an embedding page's frame-src (which Firefox also enforces on frames
 * created inside the embedded planner) would block a blob: frame, while srcdoc is never fetched.
 */
function loadFrame(html) {
  const frame = h('iframe', {
    srcdoc: html,
    title: 'Worksheet',
    'aria-hidden': 'true',
    tabindex: -1,
    style: `position:fixed;left:-10000px;top:0;width:${SHEET_PX}px;height:1056px;border:0`,
  });
  return new Promise((resolve) => {
    frame.addEventListener('load', () => resolve({ frame, cleanup: () => frame.remove() }));
    document.body.append(frame);
  });
}

async function printHtml(html) {
  const { frame, cleanup } = await loadFrame(html);
  frame.contentWindow.focus();
  frame.contentWindow.print();
  setTimeout(cleanup, 60000);
}

/** Rasterize the worksheet at full printable width with page margins, as the browser prints it (libraries load only when used). */
async function downloadPdf(html, filename) {
  const [{ default: html2canvas }, { jsPDF }, { frame, cleanup }] = await Promise.all([
    import('html2canvas'),
    import('jspdf'),
    loadFrame(html),
  ]);
  try {
    const body = frame.contentDocument.body;
    const pad = parseFloat(frame.contentWindow.getComputedStyle(body).paddingLeft) || 0;
    const box = body.getBoundingClientRect();
    const [left, top] = [box.left + pad, box.top + pad];
    // Crop the screen padding so the content box maps exactly onto the printable area.
    // 3x (~288 dpi) and lossless PNG keep the edit QR code's module edges sharp enough to scan.
    const canvas = await html2canvas(body, {
      scale: 3, backgroundColor: '#ffffff', windowWidth: SHEET_PX,
      x: left, y: top, width: box.width - 2 * pad, height: box.height - 2 * pad,
      // html2canvas draws text a little lower than the browser and ignores line-clamp (it relies on the
      // clip box), so clamped calendar titles lost their descenders (gjpqy). In its copy, cut each title
      // to the lines it shows plus "…", then give the clip box room below without moving layout.
      onclone: (doc) => {
        for (const t of doc.querySelectorAll('.ev .title')) {
          const lineHeight = parseFloat(doc.defaultView.getComputedStyle(t).lineHeight);
          const shown = Math.max(1, Math.round(t.clientHeight / lineHeight)) * lineHeight;
          const full = t.textContent;
          t.style.cssText += ';display:block;-webkit-line-clamp:none;line-clamp:none;overflow:visible';
          let n = full.length;
          while (n > 0 && t.scrollHeight > shown + 1) t.textContent = `${full.slice(0, --n).trimEnd()}…`;
          t.style.cssText += ';overflow:hidden;padding-bottom:3px;margin-bottom:-3px';
        }
      },
    });
    const pdf = new jsPDF({ unit: 'in', format: 'letter' });
    const width = PAGE.width - 2 * PAGE.side;
    const perInch = canvas.width / width;
    const slice = Math.floor((PAGE.height - 2 * PAGE.top) * perInch);
    // Taller worksheets (many section notes) continue on further pages instead of shrinking.
    for (let y = 0; canvas.height - y > 2; y += slice) {
      if (y) pdf.addPage();
      const part = document.createElement('canvas');
      part.width = canvas.width;
      part.height = Math.min(slice, canvas.height - y);
      part.getContext('2d').drawImage(canvas, 0, -y);
      pdf.addImage(part.toDataURL('image/png'), 'PNG', PAGE.side, PAGE.top, width, part.height / perInch, undefined, 'FAST');
    }
    // The image drops the worksheet's links (the edit QR code and the planner URL); put them back as
    // PDF link annotations. Page content is laid out at 96 CSS px per inch, as the browser prints it.
    const pageCss = slice / perInch * 96;
    for (const a of body.querySelectorAll('a[href^="http"]')) {
      const r = a.getBoundingClientRect();
      const page = Math.floor((r.top - top) / pageCss);
      pdf.setPage(page + 1);
      pdf.link(PAGE.side + (r.left - left) / 96, PAGE.top + (r.top - top - page * pageCss) / 96, r.width / 96, r.height / 96, { url: a.href });
    }
    // Not pdf.save(): its fallback opens a blob: frame, which an embedding page's frame-src blocks.
    download(filename, pdf.output('blob'), 'application/pdf');
  } finally {
    cleanup();
  }
}

// ------------------------------------------------------------------ toolbar

/** The schedule-only link (focused semester's classes and programs; no grades, notes, or history). */
async function shareUrl() {
  return `${location.origin}/courses/#plan=${await P.encodeShare(S.plan, S.offerings)}`;
}

async function share() {
  let url;
  try {
    url = await shareUrl();
  } catch (err) {
    status(err.message, { tone: 'error' });
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    status('Schedule link copied. It includes this semester’s classes and programs, not your academic history.');
  } catch {
    prompt('Copy this link:', url);
  }
}

/**
 * Save a file. Embedded, the host page saves it: Firefox and Safari check the host page's CSP
 * frame-src for downloads started inside this frame, and hosts typically list no blob:.
 */
function download(name, data, type) {
  const blob = new Blob([data], { type });
  if (bridgeOrigin && parentDownloads) return requestDownload(bridgeOrigin, name, blob);
  const url = URL.createObjectURL(blob);
  h('a', { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

const ACTIONS = {
  programs: () => programsDialog(),
  custom: () => customDialog(),
  print: () => worksheetDialog('print'),
  download: () => worksheetDialog('download'),
  share,
  export: () => download('obu-plan.json', JSON.stringify(S.plan, null, 2), 'application/json'),
  import: () => $('#file-json').click(),
  reset: () => {
    if (!confirm('Clear your classes and courses?')) return;
    commit({ ...P.defaultPlan(S.plan.catalog_year), programs: S.plan.programs, focus_term: S.plan.focus_term });
    status('Cleared.', { undoable: true });
  },
};

function wire() {
  window.addEventListener('focus', () => { if (!document.hidden) refreshSchedule(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshSchedule(); });
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (btn && ACTIONS[btn.dataset.action]) {
      btn.closest('details')?.removeAttribute('open');
      ACTIONS[btn.dataset.action]();
    }
    const tab = e.target.closest('[data-tab]');
    if (tab) showTab(tab.dataset.tab);
  });
  let t = 0;
  $('#q').addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(renderFind, 150);
  });
  $('#subject').addEventListener('change', renderFind);
  $('#f-program').addEventListener('change', renderFind);
  $('#f-fits').addEventListener('change', renderFind);
  $('#semester').addEventListener('change', (e) => {
    mutate((plan) => (plan.focus_term = e.target.value));
    S.open = null;
  });
  $('#file-json').addEventListener('change', async (e) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f) return;
    status('Opening plan and checking the latest cached schedule…');
    try {
      const plan = P.cleanPlan(JSON.parse(await f.text()));
      const offerings = await api.getOfferings();
      if (plan.catalog_year !== S.plan.catalog_year) await loadIndex(plan.catalog_year);
      if (!plan.focus_term) plan.focus_term = S.plan.focus_term;
      S.offerings = offerings;
      S.check = null;
      S.info.clear();
      commit(plan);
      status('Plan opened and checked against the latest cached schedule.', { undoable: true });
    } catch (err) {
      status(`Couldn't open that file (${err.message}).`, { tone: 'error' });
    }
  });
}

// ------------------------------------------------------------------ boot

/** Only regular Fall and Spring semesters are planned here (no Winter, May, or Summer terms). */
const regularSemesters = () => (S.offerings?.semesters ?? []).filter((s) => /(FA|SP)$/.test(s.term_id ?? ''));

function renderSemesters() {
  const sel = $('#semester');
  const semesters = regularSemesters();
  sel.replaceChildren(...semesters.map((s) => h('option', { value: s.term_id, selected: s.term_id === focus() }, s.label)));
  // Keep saved semesters accessible even after they leave the snapshot; never move a saved schedule.
  const saved = new Set(S.plan.terms.filter((t) => t.courses.length && /(FA|SP)$/.test(t.id)).map((t) => t.id));
  if (focus()) saved.add(focus());
  for (const id of saved) {
    if (!semesters.some((s) => s.term_id === id)) {
      sel.append(h('option', { value: id, selected: id === focus() }, termName(id)));
    }
  }
}

function renderSubjects() {
  const subjects = [...new Set(offeredCodes().map((c) => c.split(' ')[0]))].sort();
  const names = new Map([...S.index.values()].map((c) => [c.subject, c.subject_name]));
  const keep = $('#subject').value;
  $('#subject').replaceChildren(h('option', { value: '' }, 'All subjects'), ...subjects.map((s) => h('option', { value: s }, names.has(s) ? `${names.get(s)} (${s})` : s)));
  $('#subject').value = subjects.includes(keep) ? keep : '';
}

function renderAll() {
  renderPill();
  $('#data-updated').textContent = S.offerings?.snapshot_at
    ? `Course data updated ${new Date(S.offerings.snapshot_at).toLocaleDateString()}`
    : '';
  renderSemesters();
  if (S.offerings) renderSubjects();
  renderSheet();
  renderFind();
  renderMajor();
}

async function loadIndex(year) {
  const { index, offline } = await api.getIndex(year);
  S.index = new Map(index.courses.map((c) => [c.code, c]));
  S.indexYear = year;
  if (offline) status('Offline: using the catalog saved in this browser.');
}

/**
 * Loading bar. Responses are gzip-streamed without a length, so progress advances per finished
 * download (schedule, catalog list, catalog) rather than per byte; the stripe shows it's still working.
 */
function loading(pct, text) {
  const box = $('#loading');
  if (pct == null) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  $('#loading-fill').style.width = `${pct}%`;
  box.querySelector('[role="progressbar"]').setAttribute('aria-valuenow', String(Math.round(pct)));
  if (text) $('#loading-text').textContent = text;
}

/** Default semester: the cached Fall/Spring semester with the most sections. */
function defaultFocus() {
  const counts = new Map(regularSemesters().map((s) => [s.term_id, 0]));
  for (const rows of Object.values(S.offerings.courses)) for (const r of rows) if (counts.has(r.term_id)) counts.set(r.term_id, counts.get(r.term_id) + 1);
  return [...counts].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

async function boot() {
  wire();
  // Embedded, the parent's saved plan wins; accept it before anything renders so the first paint
  // already reflects the on-site plan. Standalone or a silent parent resolves to the local copy.
  const embedding = await initBridge();
  bridgeOrigin = embedding.origin;
  parentDownloads = embedding.downloads;
  let plan = P.loadPlan();
  if (embedding.plan) {
    try {
      plan = P.cleanPlan(JSON.parse(embedding.plan));
    } catch {
      /* the parent's stored plan is unreadable: keep this browser's copy */
    }
  }
  let sharedSchedule = null;
  const shared = location.hash.match(/^#plan=([A-Za-z0-9_-]+)$/);
  if (shared) {
    try {
      const incoming = await P.decodeShare(shared[1]);
      const has = plan && (plan.terms.some((t) => t.courses.length) || plan.unplaced.length);
      if (!has || confirm('Open this shared schedule? It replaces the plan saved in this browser (you can undo).')) {
        if (plan) S.undo.push(JSON.stringify(plan));
        sharedSchedule = incoming;
        plan = { ...P.defaultPlan(incoming.catalog_year), focus_term: incoming.focus_term, programs: incoming.programs };
      }
    } catch (err) {
      status(`Couldn’t open that schedule link (${err.message}).`, { tone: 'error' });
    }
    history.replaceState(null, '', location.pathname);
  }
  S.plan = plan ?? P.defaultPlan();
  const steps = { schedule: 'the class schedule', catalog: `the ${S.plan.catalog_year} course list`, list: 'majors and minors' };
  const pending = new Set(Object.keys(steps));
  const weight = { schedule: 45, catalog: 30, list: 10 };
  let pct = 5;
  let failed = false; // a download finishing after a failure must not bring the bar back over the error
  loading(pct, 'Loading the class schedule and catalog…');
  const step = (name) => (value) => {
    if (failed) return value;
    pending.delete(name);
    pct += weight[name];
    const next = [...pending].map((k) => steps[k]);
    loading(pct, next.length ? `Loading ${next.join(' and ')}…` : 'Checking your plan…');
    return value;
  };
  // All three downloads run at once; the catalog year is already known from the saved plan.
  const indexLoad = loadIndex(S.plan.catalog_year).then(step('catalog'));
  indexLoad.catch(() => {}); // reported below, after the schedule
  try {
    [S.catalogs, S.offerings] = await Promise.all([
      api.getCatalogs().then(step('list')),
      api.getOfferings().then(step('schedule')),
    ]);
  } catch (err) {
    root.dataset.state = 'error';
    failed = true;
    loading(null);
    status(`The class schedule or program list couldn't load (${err.message}). Try again later.`, { tone: 'error' });
    return;
  }
  if (sharedSchedule) {
    try {
      S.plan = P.restoreShare(sharedSchedule, S.offerings);
    } catch (err) {
      root.dataset.state = 'error';
      failed = true;
      loading(null);
      status(`Couldn’t open the shared schedule (${err.message}). Your saved plan hasn’t changed; reload to return to it.`, { tone: 'error' });
      return;
    }
  }
  if (!S.plan.focus_term) S.plan.focus_term = defaultFocus();
  ensureFocusTerm(S.plan);
  try {
    await indexLoad;
  } catch (err) {
    root.dataset.state = 'error';
    failed = true;
    loading(null);
    status(`The course catalog couldn't load (${err.message}). Try again later.`, { tone: 'error' });
    return;
  }
  root.dataset.state = 'ready';
  persist();
  renderAll();
  await runCheck();
  loading(null);
  // Ask for the major first whenever none is chosen (once per browser session, so "Skip" sticks).
  if (!S.plan.programs.some((id) => id.startsWith('major-')) && !sessionStorage.getItem('obu-planner:asked')) {
    sessionStorage.setItem('obu-planner:asked', '1');
    programsDialog(true);
  }
}

boot();
