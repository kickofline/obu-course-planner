# Working on OBU Course Planner

- Python package: `src/obu_course_planner`. Run `uv sync --locked` and `uv run pytest`.
- Frontend: Astro in `frontend/`; run `npm ci`, `npm test`, and `npm run build` there. The build writes the gitignored Python package `static/` directory, served at `/courses/`; API calls are same-origin.
- Host pages use `frontend/public/embed.js`, not a separate planner implementation. Preserve existing host-local plans through the source/origin-checked bridge before rendering. Keep `frame-ancestors` in the HTTP response, not a meta tag, and keep iframe dialogs within the visible viewport.
- `server.py` creates the Starlette app; `planner_api.py` defines `/api/planner/v1` routes.
- `planner.py` checks prerequisites and degree requirements using the reviewed JSON bundles in `data/planner`.
- `schedule.py` reads the public schedule through `cfsolve_transport.py` and a pinned native cfsolve helper (`deploy/cfsolve.patch`); `refresh.py` validates complete captures and replaces snapshots atomically. Failed refreshes must preserve the previous snapshot. The native solver is explicitly user-authorized: only the initial GET may attempt clearance, with one persistent client/cookie jar/fingerprint, no IP rotation, no retries, and no solving later challenges. Keep one shared limiter below redirects and challenge traffic at one outbound request per second, with the 400-request capture budget. Never log clearance tokens/cookies or run a solver on the request-serving API path.
- Two images: `Dockerfile` (API + wheel-bundled site; no solver or browser) and `Dockerfile.refresh` (Python 3.13 + native helper from pinned, checksummed cfsolve source including Windows PR #2). Production is Linux ARM64; do not claim Windows runtime verification without exercising it. Keep the worker's versioned tag and `coolify.managed=true` label so generic unused-image cleanup cannot remove it.
- `worksheet.py` renders self-contained printable HTML. Escape all untrusted text and preserve the schedule/calendar time rules.
- Deployment-specific origins, URLs, request identity, paths and resource identifiers belong in environment configuration, never source or committed data. Document new settings in `.env.example`; never commit `.env` or real deployment values.
- Do not add AI, model integrations, MCP endpoints, or server-side student-data persistence. Browser-local plans are intentional; API checks and worksheets process them in memory without storing or logging them.
- Keep tests deterministic and isolated from the network. Exercise changed API behavior locally as well as running tests. Use synthetic names and example domains in fixtures.
- Catalog bundles and schedule seeds contain public academic data, not personal student plans. Do not replace production snapshots with an older packaged seed.
- Keep the README short; document operational settings in `.env.example` and code comments. Do not publish infrastructure details or credentials.
