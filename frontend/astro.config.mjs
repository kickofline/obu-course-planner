import { defineConfig } from 'astro/config';

// Standalone build of the OBU course planner frontend. It is published as its own site at
// <host>/courses/ (the same origin serves the planner API under /api/planner/v1), and the personal
// site embeds it in an iframe via frontend/public/embed.js.
export default defineConfig({
  base: '/courses',
  output: 'static',
  // Build into the Python package so the standalone server can serve it from one artifact.
  outDir: '../src/obu_course_planner/static',
  vite: {
    // planner.js imports these lazily; pre-bundle them at dev-server start so a later first use
    // does not trigger a dependency re-optimization that breaks already-open tabs.
    optimizeDeps: { include: ['jspdf', 'html2canvas'] },
    // Same-origin API in dev; the backend's default bind is IPv4 loopback on :8000.
    server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  },
});