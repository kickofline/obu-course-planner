"""HTTP server for the OBU course planner API and the built /courses/ site."""
import argparse
import os
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
import uvicorn

from . import config
from .planner import PLANNER_DIR, PlannerData
from .planner_api import Offerings, routes as planner_routes
from .schedule import SOURCE_URL
from .snapshot import SNAPSHOT_PATH, SnapshotScheduleClient

# Built Astro site, produced by `npm run build` in frontend/ (see Dockerfile and README).
STATIC_DIR = Path(__file__).with_name("static")

schedule = SnapshotScheduleClient(Path(os.environ.get("OBU_SCHEDULE_SNAPSHOT", SNAPSHOT_PATH)))
planner_data = PlannerData(Path(os.environ.get("OBU_PLANNER_DATA", PLANNER_DIR)))


async def service_info(request: Request):
    return JSONResponse({
        "service": "OBU course planner API (independent; not an official university service)",
        "site": config.site_url() or None,
        "source": "https://github.com/kickofline/obu-course-planner",
        "api": "/api/planner/v1",
        "ui": "/courses/",
        "schedule_source": SOURCE_URL,
        "data_notice": "Cached schedule data may be out of date. Check snapshot_at and the schedule source.",
    })


async def healthz(request: Request):
    return JSONResponse({"status": "ok"})


def _is_courses_path(path: str) -> bool:
    return path == "/courses" or path.startswith("/courses/")


class FrameAncestorsMiddleware:
    """Restrict which origins may embed the planner UI in an iframe.

    Tightens the Content-Security-Policy of every /courses response (page and assets) to
    ``frame-ancestors 'self'`` plus the configured embedder origins. Any narrower CSP the Astro
    build ships as an HTML meta tag still applies on top of this header.
    """

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or not _is_courses_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        directive = config.frame_ancestors()

        async def send_with_csp(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                merged, seen = [], False
                for name, value in headers:
                    if name.lower() == b"content-security-policy":
                        # Keep any other CSP directives the response (or a proxy) set, but drop its
                        # frame-ancestors so ours is the one that governs embedding.
                        kept = [d.strip() for d in value.decode("latin-1").split(";")
                                if d.strip() and not d.strip().lower().startswith("frame-ancestors")]
                        value = "; ".join([*kept, directive]).encode("latin-1")
                        seen = True
                    merged.append((name, value))
                if not seen:
                    merged.append((b"content-security-policy", directive.encode("latin-1")))
                message["headers"] = merged
            await send(message)

        await self.app(scope, receive, send_with_csp)


def create_app(static_dir: Path | None = None) -> Starlette:
    """Build the app. ``static_dir`` defaults to the packaged build; tests may point elsewhere.

    When no built frontend is present (API-only checkout), /courses/ is simply absent instead of
    serving a placeholder: run the frontend build before expecting the page.
    """
    directory = STATIC_DIR if static_dir is None else static_dir
    routes: list[BaseRoute] = [
        Route("/", service_info, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        *planner_routes(schedule, planner_data, Offerings(schedule)),
    ]
    if directory.is_dir():
        routes.append(Mount("/courses", app=StaticFiles(directory=directory, html=True), name="courses"))
    app = Starlette(routes=routes)
    app.add_middleware(FrameAncestorsMiddleware)
    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="OBU course planner API")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8000, help="HTTP listen port")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()