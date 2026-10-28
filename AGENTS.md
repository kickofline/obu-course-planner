# Working on OBU Course Planner

- Python package: `src/obu_course_planner`. Run `uv sync --locked` and `uv run pytest`.
- `server.py` creates the Starlette app; `planner_api.py` defines `/api/planner/v1` routes.
- `planner.py` checks prerequisites and degree requirements using the reviewed JSON bundles in `data/planner`.
- `schedule.py` reads the public schedule; `refresh.py` validates complete captures and replaces snapshots atomically. Failed refreshes must preserve the previous snapshot. Never bypass upstream challenges or add retry loops.
- `worksheet.py` renders self-contained printable HTML. Escape all untrusted text and preserve the schedule/calendar time rules.
- Deployment-specific origins, URLs, request identity, paths and resource identifiers belong in environment configuration, never source or committed data. Document new settings in `.env.example`; never commit `.env` or real deployment values.
- Do not add AI, model integrations, MCP endpoints or student-data persistence.
- Keep tests deterministic and isolated from the network. Exercise changed API behavior locally as well as running tests. Use synthetic names and example domains in fixtures.
- Catalog bundles and schedule seeds contain public academic data, not personal student plans. Do not replace production snapshots with an older packaged seed.
- Keep the README short; document operational settings in `.env.example` and code comments. Do not publish infrastructure details or credentials.
