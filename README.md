# OBU Course Planner

Pick classes, see your week, check prerequisites and degree requirements, and print a
pre-registration worksheet. One source tree: an Astro frontend in `frontend/` and a Starlette
backend in `src/obu_course_planner/` that serves both the JSON API and the built site at `/courses/`.

**Try it:** [drewdettmer.com/courses](https://drewdettmer.com/courses)

The API uses public Ouachita Baptist University catalogs and cached class schedules. Offerings and
seat counts can change; confirm them with OBU before registering. This is an independent project,
not an official university service. Plans are processed in memory and never stored on the server.
There is no AI: no model calls, MCP endpoints, or server-side storage of student plans.

## Interface

The worksheet stays first, with class search alongside it on desktop and below it on phones.
Course swatches match their timetable blocks; neutral controls keep those course colors meaningful.
Instructor review links appear in expanded section details, not in worksheet rows.
Narrow phone calendars scroll horizontally and support keyboard navigation instead of clipping labels.
The visual polish follows [unslop](https://github.com/heydamir/unslop), Damir Sokolovsky's
[@heydamir](https://github.com/heydamir) design workflow, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

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

Downloads (worksheet PDFs and plan files) are saved by the host page: the child hands the finished
file to the loader, which accepts only a PDF or JSON blob with a plain file name from its own frame.
Firefox and Safari check the host page's CSP `frame-src` for downloads started inside the frame, so
a host CSP that allows only the planner origin would otherwise block them; the host needs no `blob:`.
Print renders the worksheet in a `srcdoc` frame, which is never fetched, so no `frame-src` applies.

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

Opening a plan file (and returning to the tab) fetches the latest cached
listing, bypassing the browser's HTTP cache, and rechecks the focused semester's schedule. A saved
section that the listing no longer has stays in the plan, marked "no longer listed" until the student
picks another section or removes the class; moved meetings show up as new time conflicts. A semester
absent from the cached listing is reported as not validated rather than unavailable, and custom
classes are only checked for overlaps.

Print and Download fill the letter page's printable width (0.4in sides, 0.5in top). Unavailable
sections are left off the worksheet and named in the dialog. The bottom-right corner carries a QR code
of the same schedule-only link as "Copy schedule link" (no grades, notes, history, name, or ID), with
the `PLANNER_SITE_URL` address printed under it; both are clickable in the PDF. The API renders the QR
only for a link of the form `https://<host>/courses/#plan=<token>`.

## Tests

```sh
uv run pytest

cd frontend && npm test   # node --test: share-link round trips, saved-schedule revalidation
```

Native transport regressions are included in `deploy/cfsolve.patch`. In the pinned upstream checkout
with that patch applied (see `Dockerfile.refresh`), run
`cargo test --locked --release --no-default-features --lib refresh::tests`.
These use local HTTP fixtures to check cookies, form posts, redirect pacing, the shared request
budget, origin restrictions, and failure without replay; they make no OBU requests.

## Deployment

Runs as a small non-root container (`python:3.13-slim`) built from `Dockerfile`; it serves the API
and the wheel-bundled site and never runs a browser. The healthcheck hits `/healthz`.

### Schedule refresh

Snapshot refreshes use a separate image (`Dockerfile.refresh`): Python plus a native
[cfsolve](https://github.com/chris124567/cfsolve) helper, not a headless browser. Upstream source is
pinned to `d8108f0e7950c8b98f582fb811f95d1f82386218` (including PR #2's Windows fixes), downloaded
with a SHA-256 check, and extended by `deploy/cfsolve.patch`. Production is Linux ARM64;
including the Windows fixes is not a claim that the native Windows helper has been runtime-tested.

One client preserves its cookie jar, Safari fingerprint, and egress for the entire capture.
Only the initial GET may attempt Cloudflare clearance. A hard block, unsupported challenge,
re-challenge, incomplete listing, or transport failure aborts the capture and preserves the last
complete snapshot. There is no IP rotation or retry loop, and no clearance cookie is transferred
to a different client. Tokens, cookies, challenge payloads, and raw upstream error chains are not logged.

The shared limiter is inside the HTTP client, below redirects and challenge requests: one outbound
request per second by default, with a 400-request budget per capture. Only the schedule and Cloudflare
challenge origins are allowed. The request-serving API image contains no native solver or browser.
Upstream changes can still break refreshes; check the refresh status and snapshot timestamp rather
than treating local tests or an old successful capture as a guarantee of current access.

```sh
# Resource-bounded native build (Rust is confined to the build stage)
docker buildx build --resource memory=4g --resource cpu-quota=100000 --load \
  -f Dockerfile.refresh -t obu-course-planner-refresh:cfsolve-d8108f0 .

# Host config, /etc/obu-course-planner/refresh.env:
#   OBU_PLANNER_REFRESH_IMAGE=sha256:<built image ID>
#   OBU_PLANNER_VOLUME=<volume holding schedule-snapshot.json>
```

`deploy/refresh_schedule.py` runs that image as a disposable, read-only, cap-dropped container
(10002:10001, 1 CPU, 1 GB RAM, no swap, no retries); `deploy/obu-course-planner-refresh.{service,timer}` run it
hourly, on the hour (UTC), without using the API container to perform captures.

The output directory must be writable by UID 10002 and readable by the API's GID 10001. Configure
the two variables above in the host environment file (mode `0600`), then install the runner and units:

```sh
sudo install -D -m 0755 deploy/refresh_schedule.py /usr/local/lib/obu-course-planner/refresh_schedule.py
sudo install -m 0644 deploy/obu-course-planner-refresh.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now obu-course-planner-refresh.timer
sudo systemctl start --no-block obu-course-planner-refresh.service
```

Use an immutable image ID for `OBU_PLANNER_REFRESH_IMAGE`; rebuild and update that value when
changing capture code. Also retain a versioned image tag: the worker is built independently of the
API, and `coolify.managed=true` keeps Coolify's generic unused-image cleanup from deleting it.
Pinning an ID alone does not retain an unused image. Verify `docker image inspect` before a cutover;
keep the last available worker image and snapshot for rollback. The API needs no restart.

For native development outside Docker, build the same pinned, patched source with Rust 1.98.0
and its CMake/Clang prerequisites, then set `OBU_CFSOLVE_EXECUTABLE` to the built `obu_refresh`
binary and `OBU_CFSOLVE_PROFILE` to its `profiles/safari-26-ios.json` file. Keep the accompanying
answer maps beside the profile. Docker supplies these paths by default.

```sh
uv run python -m obu_course_planner.refresh --output /tmp/schedule-snapshot.json --delay 1
```

The default interval is one second. For an explicitly authorized faster capture, `--delay 0.1`
permits at most ten requests per second; intervals below 0.1 seconds are rejected. This local
override does not change the installed host runner's `--delay 1`.

Each validated capture records a compact section-level delta in `history/<id>.json` beside the
snapshot. It includes added/removed section records and before/after values for changed fields
(seats, times, rooms, instructors, notes, program membership, etc.). Identity is semester plus the
existing sequence/department/course key; row order, program order, and capture timestamps do not
count as section changes. Semester coverage is recorded before/after, so a term leaving the
upcoming-only capture scope is distinguishable from a section disappearing within a retained term.
Unchanged captures are also recorded with zero counts; `refresh-status.json` includes the latest
`history_id` and `delta_counts`. The first tracked capture establishes a baseline rather than
claiming every existing section was newly added.

History records are written and synced before the snapshot's atomic replacement commits its
`history_id`. Read the linked history, not every file in the directory: interrupted publication
can leave unreferenced records, which are not committed history. A failed capture/publication
does not advance the head. Only compact deltas are retained, not a full snapshot every hour;
there is no automatic history pruning. Back up/restore the whole schedule volume, including
both the current snapshot and `history/`.

Read committed history as JSON Lines, newest first, without making any upstream requests:

```sh
uv run python -m obu_course_planner.refresh --output /path/to/schedule-snapshot.json --history

# On the deployed host, as root (the env file contains the image/volume references):
. /etc/obu-course-planner/refresh.env
docker run --rm --network none --read-only --user 10002:10001 \
  --memory 256m --memory-swap 256m --cpus 1 --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=volume,source=$OBU_PLANNER_VOLUME,target=/data/schedule,readonly" \
  "$OBU_PLANNER_REFRESH_IMAGE" --output /data/schedule/schedule-snapshot.json --history
```


## License

MIT (see [LICENSE](LICENSE)) covers this project's code. The cached OBU catalog and schedule content
and all third-party dependencies remain under their owners' terms; nothing here relicenses
university data.