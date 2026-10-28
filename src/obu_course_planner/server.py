"""HTTP server for the OBU course planner API."""
import argparse
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from . import config
from .planner import PLANNER_DIR, PlannerData
from .planner_api import Offerings, routes as planner_routes
from .schedule import SOURCE_URL
from .snapshot import SNAPSHOT_PATH, SnapshotScheduleClient

schedule = SnapshotScheduleClient(Path(os.environ.get("OBU_SCHEDULE_SNAPSHOT", SNAPSHOT_PATH)))
planner_data = PlannerData(Path(os.environ.get("OBU_PLANNER_DATA", PLANNER_DIR)))


async def service_info(request: Request) -> JSONResponse:
    return JSONResponse({
        "service": "OBU course planner API (independent; not an official university service)",
        "site": config.site_url() or None,
        "source": "https://github.com/kickofline/obu-course-planner",
        "api": "/api/planner/v1",
        "schedule_source": SOURCE_URL,
        "data_notice": "Cached schedule data may be out of date. Check snapshot_at and the schedule source.",
    })


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = Starlette(routes=[
    Route("/", service_info, methods=["GET"]),
    Route("/healthz", healthz, methods=["GET"]),
    *planner_routes(schedule, planner_data, Offerings(schedule)),
])


def main() -> None:
    parser = argparse.ArgumentParser(description="OBU course planner API")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8000, help="HTTP listen port")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
