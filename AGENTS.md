# Working on OBU Course Planner

- Python package: `src/obu_course_planner`. Run `uv sync --locked` and `uv run pytest`.
- Frontend: Astro in `frontend/`; run `npm ci`, `npm test`, and `npm run build` there. The build writes the gitignored Python package `static/` directory, served at `/courses/`; API calls are same-origin.
- Host pages use `frontend/public/embed.js`, not a separate planner implementation. Preserve existing host-local plans through the source/origin-checked bridge before rendering. Keep `frame-ancestors` in the HTTP response, not a meta tag, and keep iframe dialogs within the visible viewport.
- `server.py` creates the Starlette app; `planner_api.py` defines `/api/planner/v1` routes.
- `planner.py` checks prerequisites and degree requirements using the reviewed JSON bundles in `data/planner`.
- `schedule.py` reads the public schedule through the paced Node browser helper (`browser/`, packaged as `src/obu_course_planner/browser/transport.cjs`); `refresh.py` validates complete captures and replaces snapshots atomically. Failed refreshes must preserve the previous snapshot. A stealth-patched headless browser is a deliberate, user-authorized choice for this upstream, which blocks plain HTTP clients; never add a CAPTCHA solver, IP rotation, or retry loops, and keep the one shared limiter at one outbound HTTP(S) request per second across every asset and redirect. Never run a browser on the request-serving API path.
- Two images: `Dockerfile` (API + wheel-bundled site; no Node, no browser) and `Dockerfile.refresh` (Python 3.13 + Node 24 + the pinned Playwright headless shell). Browser driver dependencies live in `browser/` with a committed lockfile (`npm ci --prefix browser`).
- `worksheet.py` renders self-contained printable HTML. Escape all untrusted text and preserve the schedule/calendar time rules.
- Deployment-specific origins, URLs, request identity, paths and resource identifiers belong in environment configuration, never source or committed data. Document new settings in `.env.example`; never commit `.env` or real deployment values.
- Do not add AI, model integrations, MCP endpoints, or server-side student-data persistence. Browser-local plans are intentional; API checks and worksheets process them in memory without storing or logging them.
- Keep tests deterministic and isolated from the network. Exercise changed API behavior locally as well as running tests. Use synthetic names and example domains in fixtures.
- Catalog bundles and schedule seeds contain public academic data, not personal student plans. Do not replace production snapshots with an older packaged seed.
- Keep the README short; document operational settings in `.env.example` and code comments. Do not publish infrastructure details or credentials.
