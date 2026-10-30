// Child side of the embedding handshake used by the personal site (frontend/public/embed.js).
//
// Standalone, the planner uses its own localStorage. When embedded, the host site's saved plan takes
// precedence and each save is mirrored back to that host. This preserves existing on-site plans
// through a cross-origin cutover; standalone and embedded copies can diverge if edited separately.
//
// The protocol is a single round trip plus unsolicited saves and downloads:
//   child -> parent   { type: 'obu-planner:ready' }
//   parent -> child   { type: 'obu-planner:init', plan: <string|null>, downloads: true }
//                     (raw localStorage value or null; downloads only from loaders that save files)
//   child -> parent   { type: 'obu-planner:save', plan: <JSON string> }   (after every real save)
//   child -> parent   { type: 'obu-planner:download', name, blob }        (PDF or plan file to save)
//
// This module touches browser globals only inside its functions, so plain Node (the sharing tests)
// can import it without a DOM.

const READY = 'obu-planner:ready';
const INIT = 'obu-planner:init';
const SAVE = 'obu-planner:save';
const DOWNLOAD = 'obu-planner:download';
// A parent that never answers must not keep the planner from booting; the frame shows the standalone
// (localStorage) plan instead. Independent embeds each pay this only when their parent is silent.
const WAIT_MS = 1500;

/** True when this page is framed by another document (the site) rather than opened directly. */
export function isEmbedded() {
  return typeof window !== 'undefined' && typeof document !== 'undefined' && window.parent !== window;
}

/**
 * The embedder's origin: document.referrer, or location.ancestorOrigins where the host page's
 * referrer policy (no-referrer) empties the referrer, as WebKit does despite the frame's own policy.
 * Null when there is none / it is unparsable.
 */
function referrerOrigin() {
  try {
    if (document.referrer) return new URL(document.referrer).origin;
    const ancestor = location.ancestorOrigins?.[0];
    return ancestor && ancestor !== 'null' ? new URL(ancestor).origin : null;
  } catch {
    return null;
  }
}

/**
 * Announce readiness to the embedding parent and await its saved plan.
 * Resolves { plan, origin, downloads }: origin is the accepted parent origin (null when standalone),
 * plan is the parent's raw localStorage string or null, and downloads says the parent saves files.
 * Never rejects; a missing/silent parent resolves null.
 */
export function initBridge() {
  const none = { plan: null, origin: null, downloads: false };
  if (!isEmbedded()) return Promise.resolve(none);
  const origin = referrerOrigin();
  if (!origin) return Promise.resolve(none);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener('message', onMessage);
      resolve(value);
    };
    const timer = setTimeout(() => finish(none), WAIT_MS);
    function onMessage(event) {
      if (event.source !== window.parent) return; // only our own embedder
      if (event.origin !== origin) return; // and only from the page named in document.referrer
      const data = event.data;
      if (!data || data.type !== INIT) return;
      if (typeof data.plan !== 'string' && data.plan !== null) return;
      finish({ plan: typeof data.plan === 'string' ? data.plan : null, origin, downloads: data.downloads === true });
    }
    window.addEventListener('message', onMessage);
    window.parent.postMessage({ type: READY }, origin);
  });
}

/**
 * Publish a save to the embedding parent. No-op until initBridge accepted a parent init (origin is
 * null standalone), so a stored plan is never sent to an arbitrary embedder.
 */
export function publishPlan(origin, plan) {
  if (!origin || typeof window === 'undefined') return;
  try {
    window.parent.postMessage({ type: SAVE, plan: JSON.stringify(plan) }, origin);
  } catch {
    /* parent navigated away: the standalone save already happened */
  }
}

/** Hand a finished file to the embedding parent to save (only after it advertised downloads). */
export function requestDownload(origin, name, blob) {
  window.parent.postMessage({ type: DOWNLOAD, name, blob }, origin);
}