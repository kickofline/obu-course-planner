# OBU Course Planner

Pick classes, see your week, check prerequisites and degree requirements, and print a
pre-registration worksheet. One source tree: an Astro frontend in `frontend/` and a Starlette
backend in `src/obu_course_planner/` that serves both the JSON API and the built site at `/courses/`.

**Try it:** [drewdettmer.com/courses](https://drewdettmer.com/courses)

The API uses public Ouachita Baptist University catalogs and cached class schedules. Offerings and
seat counts can change; confirm them with OBU before registering. This is an independent project,
not an official university service. Plans are processed in memory and never stored on the server.
There is no AI: no model calls, MCP endpoints, or server-side storage of student plans.

## Layout

- `frontend/` — Astro site (base `/courses`); modules under `frontend/src/scripts/courses/`.
- `src/obu_course_planner/` — API, worksheet renderer, and the generated `static/` build the server serves.
- `tests/` — backend pytest suite; `frontend/tests/` — `node --test` suite.

The frontend calls `/api/planner/v1` on its own origin by default, so the standalone site needs no
configuration. `PUBLIC_PLANNER_API` overrides the API base for development only.

## Run locally

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and Node 22 (>= 22.12) for the frontend.

```sh
# Backend (API only; /courses/ appears once the frontend has been built)
uv sync --locked
uv run obu-course-planner --host 127.0.0.1 --port 8000

# Frontend dev server (proxies to the backend API)
cd frontend && npm ci && npm run dev
```

To serve the built site from the backend at `/courses/`, build the frontend first — it writes into
`src/obu_course_planner/static/`, which the server mounts:

```sh
cd frontend && npm ci && npm run build
```

Configuration is through environment variables; see [.env.example](.env.example). Export them in
your shell or deployment platform; the app does not load `.env` files.

## API

Under `/api/planner/v1`: catalog years, course index and details, offerings, plan checks, and
printable worksheets. `/healthz` checks the server; `/` returns service info. The Docker image builds
the frontend and serves the wheel-bundled site at `/courses/` on port 8000.

## Embedding

Any page that wants the planner inline loads the classic loader script (no imports, no bundler) from
the same origin as `data-src`, and marks a container:

```html
<div data-obu-planner data-src="https://planner.example/courses/"></div>
<script src="https://planner.example/courses/embed.js"></script>
```

The loader mounts a full-viewport iframe (`height: 100dvh`) rather than an auto-height frame, so the
planner's dialogs stay inside the visible viewport instead of drifting off long mobile pages. When
embedded, the host page's own saved plan (`localStorage` key `obu-planner:v1`) takes precedence: the
child initializes from the parent's copy and mirrors later saves back into it, so a plan made on the
host page carries over. This is a handoff plus write-back, not live cross-origin synchronization;
edits made directly on the standalone page do not update a closed host page. The bridge exchanges
data only between browser windows. Plan checks and worksheet generation still send the plan to the
API for in-memory processing, without storing it. The child accepts initialization only from its own
parent (`document.referrer`) and publishes only after that handshake. HTTP `frame-ancestors` restricts
embedding to `'self'` and the configured trusted origins.

Because the two host pages live on different origins, list both the standalone origin (where
`/courses/` is served, behind your proxy) and every embedding origin in `PLANNER_ALLOWED_ORIGINS`.

## Sharing and export

"Copy schedule link" produces a compact, self-contained link (a `#plan=` fragment, decoded entirely
in the browser — no shortening service) carrying the focused schedule and selected programs only. It
deliberately omits grades, private notes, academic history, and unplaced courses. The codec reads
only this compact schedule payload; links from older versions that embedded a full plan are not
accepted — regenerate those links from the current page. "Download plan file" exports the full plan
as JSON, which can be imported back on any browser or device to move an entire plan (including
grades, notes, and history) between the standalone page and an embed.

## Tests

```sh
uv run pytest

cd frontend && npm test   # node --test, share-link round trips

# Refresh browser pacing (no upstream traffic)
cd ../browser && npm ci && npm test
```

## Deployment

Runs as a small non-root container (`python:3.13-slim`) built from `Dockerfile`; it serves the API
and the wheel-bundled site and never runs a browser. The healthcheck hits `/healthz`.

### Schedule refresh

Snapshot refreshes use a separate image (`Dockerfile.refresh`), because the capture drives a headless
browser. CSS, images, fonts, and media are blocked before reaching the network. HTML, JavaScript,
fetch/XHR, and frame documents remain enabled for verification and form behavior. A Node helper
(`browser/`) paces every remaining outbound HTTP(S) request — including redirects — to at most one
per second; no browser ever runs on the request-serving path. The transport is a stealth-patched
headless browser against an upstream that blocks plain HTTP clients: there is no CAPTCHA solving,
no IP rotation, and no retry loop. A capture that fails or returns an incomplete listing is discarded,
and the last complete snapshot stays in place.

Stealth does not guarantee upstream access: Cloudflare may still return HTTP 403. Check the refresh
service journal and the snapshot's capture timestamp before treating cached offerings as current;
passing the local browser regression does not establish upstream availability.

```sh
# Build the refresher (Node 24 + pinned Playwright headless shell baked in)
docker build -f Dockerfile.refresh -t obu-course-planner-refresh:local .

# Host config, /etc/obu-course-planner/refresh.env:
#   OBU_PLANNER_REFRESH_IMAGE=obu-course-planner-refresh:local
#   OBU_PLANNER_VOLUME=<volume holding schedule-snapshot.json>
```

`deploy/refresh_schedule.py` runs that image as a disposable, read-only, cap-dropped container
(`10002:10001`, 1 CPU, 1 GB, no retries); `deploy/obu-course-planner-refresh.{service,timer}` run it
daily at 05:00 UTC, without touching the running API container.

The output directory must be writable by UID 10002 and readable by the API's GID 10001. Configure
the two variables above in the host environment file (mode `0600`), then install the runner and units:

```sh
sudo install -D -m 0755 deploy/refresh_schedule.py /usr/local/lib/obu-course-planner/refresh_schedule.py
sudo install -m 0644 deploy/obu-course-planner-refresh.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now obu-course-planner-refresh.timer
sudo systemctl start --no-block obu-course-planner-refresh.service
```

Use a commit-tagged image or immutable image ID for `OBU_PLANNER_REFRESH_IMAGE`; rebuild and update
that value when changing the capture code. The API image is deployed independently.

For local refresher development, install the locked driver dependencies and matching Chromium
(the Docker image uses its headless shell), then point the transport at it:

```sh
npm ci --prefix browser
node browser/node_modules/playwright-core/cli.js install chromium
export NODE_PATH="$PWD/browser/node_modules"
export OBU_BROWSER_EXECUTABLE="$(node -e "console.log(require('playwright-core').chromium.executablePath())")"
uv run python -m obu_course_planner.refresh --output /tmp/schedule-snapshot.json --delay 1
```

With those browser variables exported, `npm test --prefix browser` also runs the real-browser
regression against a local HTTP fixture (no OBU requests), including native JavaScript navigation,
cookies, POST submission, blocked presentation assets (including in child frames), and request pacing.

## License

MIT (see [LICENSE](LICENSE)) covers this project's code. The cached OBU catalog and schedule content
and all third-party dependencies remain under their owners' terms; nothing here relicenses
university data.