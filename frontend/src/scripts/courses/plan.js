// Plan model, term ids, persistence, and share links. Pure functions except load/save.

export const STORAGE_KEY = 'obu-planner:v1';
const SEASON_ORDER = { WI: 0, SP: 1, MA: 2, SU: 3, FA: 4 };
const SEASON_LABEL = { WI: 'Winter', SP: 'Spring', MA: 'May Term', SU: 'Summer', FA: 'Fall' };
const CODE = /^[A-Z]{2,5} \d{3,4}(?:\.5|[A-Z])?$/; // 1080.5 = half-hour course

export function termKey(id) {
  if (id === 'TR') return -1;
  const m = /^(\d{4})(WI|SP|MA|SU|FA)$/.exec(id);
  return m ? Number(m[1]) * 10 + SEASON_ORDER[m[2]] : NaN;
}

export function termLabel(id) {
  if (id === 'TR') return 'Transfer / AP credit';
  if (id === 'UNPLACED') return 'Unplaced';
  const m = /^(\d{4})(WI|SP|MA|SU|FA)$/.exec(id);
  return m ? `${SEASON_LABEL[m[2]]} ${m[1]}` : id;
}

export function isTermId(id) {
  return id === 'TR' || !Number.isNaN(termKey(id));
}

/** Term containing a date (catalog calendar windows). */
export function termForDate(d = new Date()) {
  const y = d.getFullYear();
  const md = (d.getMonth() + 1) * 100 + d.getDate();
  if (md >= 801 && md <= 1220) return `${y}FA`;
  if (md >= 1221) return `${y + 1}WI`;
  if (md <= 110) return `${y}WI`;
  if (md <= 509) return `${y}SP`;
  if (md <= 531) return `${y}MA`;
  return `${y}SU`;
}

export function normalizeCode(code) {
  const text = String(code ?? '').toUpperCase().replace(/\s+/g, ' ').trim();
  const m = /^([A-Z]{2,5}) ?(\d{3,4}(?:\.5|[A-Z])?)(?:\.0)?$/.exec(text);
  return m ? `${m[1]} ${m[2]}` : text;
}

export function isCode(code) {
  return CODE.test(code);
}

/** Empty plan: transfer credit plus the semester being registered for (focus_term, chosen once offerings load). */
export function defaultPlan(year = '2026-27') {
  return { version: 1, catalog_year: year, programs: ['core'], focus_term: null, terms: [{ id: 'TR', courses: [] }], unplaced: [] };
}

/** Sanitize anything loaded from storage, files, or links into a valid plan. */
export function cleanPlan(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('Not a plan file.');
  const entry = (c) => {
    const code = normalizeCode(c?.code);
    if (!isCode(code)) return null;
    const out = { code };
    if (typeof c.hours === 'number' && c.hours >= 0 && c.hours <= 12) out.hours = c.hours;
    if (typeof c.grade === 'string' && c.grade.trim()) out.grade = c.grade.trim().toUpperCase().slice(0, 4);
    if (typeof c.note === 'string' && c.note.trim()) out.note = c.note.trim().slice(0, 160);
    if (typeof c.seq === 'string' && /^\d{1,6}$/.test(c.seq)) out.seq = c.seq;
    // Custom class (not on OBU's schedule): own title and optional meeting (days + minutes after midnight).
    if (c.custom === true && !out.seq) {
      out.custom = true;
      if (typeof c.title === 'string' && c.title.trim()) out.title = c.title.trim().slice(0, 80);
      const timed = Number.isInteger(c.start) && Number.isInteger(c.end) && c.start >= 0 && c.end > c.start && c.end < 1440 && c.end - c.start <= 360;
      if (timed && typeof c.days === 'string' && /^(?:TH|M|T|W|F|S)+$/.test(c.days)) Object.assign(out, { days: c.days, start: c.start, end: c.end });
    }
    return out;
  };
  const seen = new Set();
  const terms = [];
  for (const t of Array.isArray(raw.terms) ? raw.terms : []) {
    if (!t || !isTermId(t.id) || seen.has(t.id)) continue;
    seen.add(t.id);
    terms.push({ id: t.id, courses: (Array.isArray(t.courses) ? t.courses : []).map(entry).filter(Boolean).slice(0, 40) });
  }
  if (!seen.has('TR')) terms.unshift({ id: 'TR', courses: [] });
  terms.sort((a, b) => termKey(a.id) - termKey(b.id));
  return {
    version: 1,
    catalog_year: /^\d{4}-\d{2}$/.test(raw.catalog_year) ? raw.catalog_year : '2026-27',
    programs: (Array.isArray(raw.programs) ? raw.programs : []).filter((p) => typeof p === 'string' && /^[a-z0-9-]{1,80}$/.test(p)).slice(0, 8),
    focus_term: typeof raw.focus_term === 'string' && isTermId(raw.focus_term) && raw.focus_term !== 'TR' ? raw.focus_term : null,
    terms: terms.slice(0, 40),
    unplaced: (Array.isArray(raw.unplaced) ? raw.unplaced : []).map(entry).filter(Boolean).slice(0, 200),
  };
}

export function loadPlan() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? cleanPlan(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

export function savePlan(plan) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(plan));
  } catch {
    /* storage full or disabled: the plan still works for this session */
  }
}

/** Request body for /check. */
export function forApi(plan) {
  return { version: 1, catalog_year: plan.catalog_year, programs: plan.programs, focus_term: plan.focus_term, terms: plan.terms, unplaced: plan.unplaced };
}

function toBase64Url(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text) {
  const s = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function pipe(bytes, stream) {
  return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(stream)).arrayBuffer());
}

/** Schedule-only shares: [catalog year, semester, program IDs, entries].
 * Ordinary entries are just section-number strings. Sparse objects carry custom/unselected classes
 * and explicit hours that differ from the schedule. No grades, notes, history, or unplaced courses.
 */
export async function encodeShare(plan, offerings) {
  const term = plan.terms.find((t) => t.id === plan.focus_term);
  if (!term || !offerings?.semesters.some((s) => s.term_id === term.id)) throw new Error('Choose a semester before sharing.');
  const entries = term.courses.map((c) => {
    const section = !c.custom && c.seq
      ? offerings.courses[c.code]?.find((s) => s.term_id === term.id && s.seq === c.seq)
      : null;
    const hours = typeof c.hours === 'number' && (!section || c.hours !== section.hours) ? { h: c.hours } : {};
    if (section) return Object.keys(hours).length ? { q: c.seq, ...hours } : c.seq;
    return {
      c: c.code,
      ...hours,
      ...(c.seq && !c.custom ? { q: c.seq } : {}),
      ...(c.custom ? { x: 1, ...(c.title ? { t: c.title } : {}), ...(c.days ? { d: c.days, s: c.start, e: c.end } : {}) } : {}),
    };
  });
  const bytes = new TextEncoder().encode(JSON.stringify([plan.catalog_year, term.id, plan.programs, entries]));
  if (typeof CompressionStream === 'function') {
    const compressed = await pipe(bytes, new CompressionStream('deflate-raw'));
    if (compressed.length < bytes.length) return 'c' + toBase64Url(compressed);
  }
  return 's' + toBase64Url(bytes);
}

/** Decode the header before fetching course data; section numbers are resolved only after offerings load. */
export async function decodeShare(token) {
  if (!/^[cs][A-Za-z0-9_-]+$/.test(token)) throw new Error('Unrecognized schedule link format.');
  const bytes = fromBase64Url(token.slice(1));
  const text = token[0] === 'c' ? await pipe(bytes, new DecompressionStream('deflate-raw')) : bytes;
  const data = JSON.parse(new TextDecoder().decode(text));
  if (!Array.isArray(data) || data.length !== 4) throw new Error('Invalid schedule link.');
  const [catalog_year, focus_term, programs, entries] = data;
  if (typeof catalog_year !== 'string' || !/^\d{4}-\d{2}$/.test(catalog_year) ||
      typeof focus_term !== 'string' || !/^\d{4}(FA|SP)$/.test(focus_term) ||
      !Array.isArray(programs) || programs.length > 8 || programs.some((p) => typeof p !== 'string' || !/^[a-z0-9-]{1,80}$/.test(p)) ||
      !Array.isArray(entries) || entries.length > 40) throw new Error('Invalid schedule link.');
  return { catalog_year, focus_term, programs, entries };
}

/** Rebuild codes from this semester's schedule, never another semester that happens to reuse a sequence.
 * Missing/ambiguous sections fail explicitly rather than dropping courses or silently choosing another one.
 */
export function restoreShare(shared, offerings) {
  const { catalog_year, focus_term, programs, entries } = shared;
  if (!offerings.semesters.some((s) => s.term_id === focus_term)) throw new Error('That semester is no longer in the cached schedule.');
  const sequences = new Map();
  for (const [code, rows] of Object.entries(offerings.courses)) {
    for (const row of rows) {
      if (row.term_id !== focus_term) continue;
      const codes = sequences.get(row.seq) ?? new Set();
      codes.add(code);
      sequences.set(row.seq, codes);
    }
  }
  const courses = entries.map((entry) => {
    const c = typeof entry === 'string' ? { q: entry } : entry;
    if (!c || typeof c !== 'object' || Array.isArray(c)) throw new Error('Invalid class in schedule link.');
    let code = c.c;
    if (code == null) {
      const codes = sequences.get(c.q);
      if (!codes || codes.size !== 1) throw new Error(`Section ${c.q ?? '?'} is missing or ambiguous in the cached schedule.`);
      code = [...codes][0];
    }
    if (!isCode(code) || (c.q != null && (typeof c.q !== 'string' || !/^\d{1,6}$/.test(c.q))) ||
        (c.h != null && (typeof c.h !== 'number' || !Number.isFinite(c.h) || c.h < 0 || c.h > 12))) throw new Error('Invalid class in schedule link.');
    return {
      code,
      ...(c.h != null ? { hours: c.h } : {}),
      ...(c.q != null && c.x !== 1 ? { seq: c.q } : {}),
      ...(c.x === 1 ? { custom: true, title: c.t, days: c.d, start: c.s, end: c.e } : {}),
    };
  });
  return cleanPlan({ catalog_year, focus_term, programs, terms: [{ id: focus_term, courses }] });
}
