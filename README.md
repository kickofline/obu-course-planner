# OBU Course Planner

Pick classes, check degree requirements and time conflicts, and print a registration worksheet.

**Try it:** [drewdettmer.com/courses](https://drewdettmer.com/courses)

The API uses public Ouachita Baptist University catalogs and cached class schedules. Offerings and seat counts can change; confirm them with OBU before registering. This is an independent project, not an official university service. Plans are processed in memory, not stored.

## Run locally

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked
uv run obu-course-planner --host 127.0.0.1 --port 8000
```

Configuration is through environment variables; see [.env.example](.env.example). Export those values in your shell or deployment platform; the app does not automatically load `.env` files.

The API is under `/api/planner/v1`: catalog years, course index/details, offerings, plan checks, and printable worksheets. `/healthz` checks the server.

```sh
uv run pytest
```

The optional refresh runner and daily systemd timer are in `deploy/`. Configure `/etc/obu-course-planner/refresh.env` before enabling them. A failed refresh keeps the last complete schedule.
