// Embed the standalone planner while preserving plans previously saved on the host site.
// Serve this script from the same trusted origin as data-src. No plans go to a server.
(() => {
  const key = 'obu-planner:v1';
  for (const mount of document.querySelectorAll('[data-obu-planner]')) {
    if (mount.querySelector('iframe')) continue;
    const url = new URL(mount.dataset.src, location.href);
    if (!['https:', 'http:'].includes(url.protocol)) continue;
    const origin = url.origin;
    if (/^#plan=[A-Za-z0-9_-]+$/.test(location.hash)) url.hash = location.hash;
    const frame = document.createElement('iframe');
    frame.title = 'OBU Pre-Registration Worksheet';
    frame.allow = 'clipboard-write';
    frame.referrerPolicy = 'origin';
    // Keep the frame viewport-sized: dialogs center in the visible viewport, even on long mobile pages.
    frame.style.cssText = 'display:block;width:100%;height:100dvh;border:0';
    window.addEventListener('message', (event) => {
      if (event.source !== frame.contentWindow || event.origin !== origin) return;
      if (event.data?.type === 'obu-planner:ready') {
        let plan = null;
        try { plan = localStorage.getItem(key); } catch { /* storage disabled */ }
        frame.contentWindow.postMessage({ type: 'obu-planner:init', plan }, origin);
        if (/^#plan=[A-Za-z0-9_-]+$/.test(location.hash)) {
          history.replaceState(null, '', location.pathname + location.search);
        }
      } else if (event.data?.type === 'obu-planner:save' && typeof event.data.plan === 'string') {
        try { localStorage.setItem(key, event.data.plan); } catch { /* storage disabled */ }
      }
    });
    mount.replaceChildren(frame);
    frame.src = url.href;
  }
})();
