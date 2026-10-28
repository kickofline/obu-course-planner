// Client for the planner API at /api/planner/v1 on this page's own origin (the standalone server
// serves the frontend and the API together). PUBLIC_PLANNER_API overrides the base for local dev.

const configured = (import.meta.env.PUBLIC_PLANNER_API || '').trim().replace(/\/+$/, '');
export const API = `${configured}/api/planner/v1`;
const INDEX_CACHE = 'obu-planner:index:';
// Earlier versions cached the whole ~1 MB catalog bundle; free that space.
try {
  for (const key of Object.keys(localStorage)) if (key.startsWith('obu-planner:bundle:')) localStorage.removeItem(key);
} catch {
  /* storage disabled */
}

async function json(res) {
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok) {
    const err = new Error(body?.error || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

export async function getCatalogs() {
  return json(await fetch(`${API}/catalogs`));
}

/** Course index (code, title, hours, subject) for a catalog year. Falls back to the copy cached in this browser. */
export async function getIndex(year) {
  try {
    const index = await json(await fetch(`${API}/${encodeURIComponent(year)}/index`));
    try {
      localStorage.setItem(INDEX_CACHE + year, JSON.stringify(index));
    } catch {
      /* quota: fine, just no offline copy */
    }
    return { index, offline: false };
  } catch (err) {
    const cached = localStorage.getItem(INDEX_CACHE + year);
    if (cached) return { index: JSON.parse(cached), offline: true };
    throw err;
  }
}

/** One course's full catalog entry (description, prerequisites), loaded when a student opens it. */
export async function getCourse(year, code) {
  return json(await fetch(`${API}/${encodeURIComponent(year)}/courses/${encodeURIComponent(code)}`));
}

export async function getOfferings() {
  return json(await fetch(`${API}/offerings`));
}

export async function checkPlan(plan, signal) {
  return json(
    await fetch(`${API}/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(plan),
      signal,
    }),
  );
}

export async function buildWorksheet(body) {
  return json(
    await fetch(`${API}/worksheet`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  );
}
