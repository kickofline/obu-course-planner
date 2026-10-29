// Meeting-time parsing and calendar layout, mirroring the API's worksheet renderer
// (obu_course_planner/worksheet.py) so the on-screen week matches the downloaded worksheet.

export const DAY_ORDER = ['M', 'T', 'W', 'TH', 'F', 'S'];
export const DAY_LABELS = { M: 'Monday', T: 'Tuesday', W: 'Wednesday', TH: 'Thursday', F: 'Friday', S: 'Saturday' };
const DAY_NAMES = { MONDAY: 'M', TUESDAY: 'T', WEDNESDAY: 'W', THURSDAY: 'TH', FRIDAY: 'F', SATURDAY: 'S' };
export const CHAPEL = { days: ['T'], start: 600, end: 650 };
// (fill, ink) pairs shared with the worksheet.
export const PALETTE = [
  ['#cfe2ff', '#0a4fb5'], ['#d1f2d9', '#17733f'], ['#ffe1b8', '#b35c00'], ['#f8d4d8', '#a52834'],
  ['#e4dbf5', '#5b3aa8'], ['#cff0f5', '#0b6676'], ['#fff1b3', '#8a6800'], ['#f6d3e5', '#962051'],
  ['#dde3ff', '#2f45b0'], ['#d8f5d0', '#2c7a1f'], ['#ffd8c2', '#c2410c'], ['#e6e6e6', '#444444'],
];

/** OBU day strings (MWF, TTH, TWTH, W&F, MONDAY) -> ordered day codes; null if not a meeting pattern. */
export function parseDays(raw) {
  let value = String(raw || '').toUpperCase().replace(/[&\s]/g, '');
  value = DAY_NAMES[value] ?? value;
  if (!value || !/^(?:TH|M|T|W|F|S)+$/.test(value)) return null;
  return [...new Set(value.match(/TH|M|T|W|F|S/g))];
}

function toMinutes(clock) {
  let [hour, minute] = clock.split(':').map(Number);
  if (hour >= 1 && hour <= 7) hour += 12; // OBU daytime/evening slots: 8:00-11:59 a.m., 12:00-7:59 p.m.
  return hour * 60 + minute;
}

/** "10:00 - 10:50" -> [600, 650]; null for TBA / 0:00 / implausible spans. */
export function parseTime(raw) {
  const m = /^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$/.exec(String(raw || ''));
  if (!m || m[1] === '0:00') return null;
  const start = toMinutes(m[1]);
  let end = toMinutes(m[2]);
  if (end <= start) end += 12 * 60;
  return end - start <= 6 * 60 ? [start, end] : null;
}

export function clock(minutes) {
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  return `${((hour + 11) % 12) + 1}:${String(minute).padStart(2, '0')}${hour < 12 ? 'a' : 'p'}`;
}

/**
 * Place sections on the week. Returns events per day with side-by-side columns, plus conflicts.
 * @param {{key: string, label: string, days: string, time: string, color: string[]}[]} items
 */
export function week(items) {
  const byDay = Object.fromEntries(DAY_ORDER.map((d) => [d, []]));
  const unscheduled = [];
  for (const item of items) {
    const days = parseDays(item.days);
    const span = item.span ?? parseTime(item.time); // custom classes carry exact minutes
    if (!days || !span) {
      unscheduled.push(item);
      continue;
    }
    for (const d of days) byDay[d].push({ item, start: span[0], end: span[1] });
  }
  if (!items.some((i) => i.label.startsWith('CHAP'))) {
    for (const d of CHAPEL.days) byDay[d].push({ item: null, start: CHAPEL.start, end: CHAPEL.end });
  }
  const conflicts = [];
  for (const [day, events] of Object.entries(byDay)) {
    for (let i = 0; i < events.length; i++) {
      for (let j = i + 1; j < events.length; j++) {
        const [a, b] = [events[i], events[j]];
        if (a.start < b.end && b.start < a.end) {
          a.conflict = b.conflict = true;
          conflicts.push({ day, a: a.item, b: b.item, from: Math.max(a.start, b.start), to: Math.min(a.end, b.end) });
        }
      }
    }
    layout(events);
  }
  const days = DAY_ORDER.filter((d) => d !== 'S' || byDay.S.length);
  const all = days.flatMap((d) => byDay[d]);
  const first = Math.min(8, ...all.map((e) => Math.floor(e.start / 60)));
  const last = Math.max(17, ...all.map((e) => Math.ceil(e.end / 60)));
  return { byDay, days, first, last, conflicts, unscheduled };
}

function layout(events) {
  events.sort((a, b) => a.start - b.start || a.end - b.end);
  let cluster = [];
  let clusterEnd = -1;
  const close = () => {
    const width = Math.max(-1, ...cluster.map((e) => e.col)) + 1;
    for (const e of cluster) e.cols = width;
  };
  for (const e of events) {
    if (cluster.length && e.start >= clusterEnd) {
      close();
      cluster = [];
      clusterEnd = -1;
    }
    const used = new Set(cluster.filter((c) => c.end > e.start).map((c) => c.col));
    e.col = 0;
    while (used.has(e.col)) e.col++;
    cluster.push(e);
    clusterEnd = Math.max(clusterEnd, e.end);
  }
  close();
}

/** Does a candidate section overlap any chosen section (or chapel)? Returns the clashing labels. */
export function clashes(candidate, chosen) {
  const days = parseDays(candidate.days);
  const span = parseTime(candidate.time);
  if (!days || !span) return [];
  const out = [];
  for (const c of chosen) {
    const cd = parseDays(c.days);
    const cs = c.span ?? parseTime(c.time);
    if (cd && cs && cd.some((d) => days.includes(d)) && span[0] < cs[1] && cs[0] < span[1]) out.push(c.label);
  }
  if (days.some((d) => CHAPEL.days.includes(d)) && span[0] < CHAPEL.end && CHAPEL.start < span[1]) out.push('Chapel');
  return out;
}

/** Recheck saved choices without replacing them. An uncached semester is unknown, not unavailable. */
export function reviewSelections(entries, termId, offerings) {
  const semester = offerings?.semesters.find((s) => s.term_id === termId) ?? null;
  const issues = [];
  if (!semester) return { semester, issues };
  for (const entry of entries) {
    if (entry.custom) continue;
    const sections = (offerings.courses[entry.code] ?? []).filter((s) => s.term_id === termId);
    if (entry.seq && !sections.some((s) => s.seq === entry.seq)) {
      issues.push({
        code: entry.code, severity: 'error', kind: 'missing-section',
        message: `Section ${entry.seq} is no longer listed for ${semester.label}. Your saved choice is kept; choose another section or remove the class.`,
      });
    } else if (!sections.length) {
      issues.push({ code: entry.code, severity: 'error', kind: 'missing-course', message: `This course is not on the ${semester.label} listing.` });
    } else if (!entry.seq) {
      issues.push({ code: entry.code, severity: 'warn', kind: 'unselected', message: 'Pick a section.' });
    }
  }
  return { semester, issues };
}
