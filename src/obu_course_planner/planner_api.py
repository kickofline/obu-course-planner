"""Public JSON API for the browser course planner.

Stateless and log-free: plans are processed in memory and never stored or logged. Cross-origin
access is limited to the origins in PLANNER_ALLOWED_ORIGINS (see config.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import re
from typing import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import config
from .planner import PLAN_CODE, PlanError, PlannerData, check_plan, label_to_term_id
from .snapshot import SnapshotScheduleClient
from .worksheet import course_label, parse_days, render as render_worksheet

PREFIX = "/api/planner/v1"
MAX_BODY = 256 * 1024
# The frontend's "Copy schedule link" shape: https://host/courses/#plan=<compact token>.
EDIT_URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?/courses/#plan=[cs][A-Za-z0-9_-]+")


def custom_rows(raw: object) -> list[dict]:
    """Student-entered classes (not on OBU's schedule) as worksheet course rows. Times are minutes after midnight."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 12:
        raise PlanError("custom must be a list of up to 12 classes.")
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            raise PlanError("Each custom class must be an object.")
        code = re.sub(r"\s+", " ", str(item.get("code") or "")).strip().upper()
        if not PLAN_CODE.match(code):
            raise PlanError(f"Custom class code {code!r} is not like 'CYBR 1003'.")
        days, start, end, hours = str(item.get("days") or ""), item.get("start"), item.get("end"), item.get("hours")
        timed = isinstance(start, int) and isinstance(end, int)
        if timed and not (0 <= start < end < 24 * 60 and end - start <= 6 * 60 and parse_days(days)):
            raise PlanError(f"Meeting time for {code} is not valid.")
        if hours is not None and (not isinstance(hours, (int, float)) or not 0 <= hours <= 12):
            raise PlanError(f"Hours for {code} must be 0-12.")
        department, number = code.split(" ")
        rows.append({
            "sequence": "", "department": department, "course": number,
            "title": str(item.get("title") or "")[:80] or "Custom class", "time_slot": "",
            "days": days if timed else "TBA", "time": "" if timed else "TBA",
            "room": "", "credit_hours": hours, "notes": [], "_span": (start, end) if timed else None,
        })
    return rows


def _cors(request: Request, response: Response) -> Response:
    origin = request.headers.get("origin")
    if origin in config.allowed_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


def _error(request: Request, status: int, message: str) -> Response:
    return _cors(request, JSONResponse({"error": message}, status_code=status))


async def read_json(request: Request) -> object:
    if request.headers.get("origin") not in (None, *config.allowed_origins()):
        raise PermissionError("Origin not allowed.")
    declared = request.headers.get("content-length")
    if declared and (not declared.isdigit() or int(declared) > MAX_BODY):
        raise OverflowError
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise OverflowError
    try:
        return json.loads(body)
    except ValueError as exc:
        raise PlanError("Request body must be JSON.") from exc


class Offerings:
    """Course-code view of the cached schedule snapshot, rebuilt when the snapshot changes."""

    def __init__(self, schedule: SnapshotScheduleClient) -> None:
        self._schedule = schedule
        self._source: dict | None = None
        self._view: dict | None = None

    def view(self) -> dict:
        snapshot = self._schedule._load()
        if snapshot is not self._source:
            self._view, self._source = self._build(snapshot), snapshot
        return self._view

    @staticmethod
    def _build(snapshot: dict) -> dict:
        semesters, courses = [], {}
        captured = []
        for semester in snapshot["semesters"]:
            dataset = snapshot["datasets"][semester["value"]]
            term_id = label_to_term_id(semester["label"])
            semesters.append({"code": semester["value"], "label": semester["label"], "term_id": term_id,
                              "snapshot_at": dataset["snapshot_at"]})
            captured.append(dataset["snapshot_at"])
            for row in dataset["courses"]:
                number = re.sub(r"\.0$", "", row["course"])  # "1003.0" -> 1003; half-hour "1080.5" stays
                if not re.fullmatch(r"\d{3,4}(?:\.5)?", number):
                    continue
                code = f"{row['department']} {number}"
                courses.setdefault(code, []).append({
                    "term_id": term_id, "semester": semester["value"], "seq": row["sequence"],
                    "title": row["title"], "days": row["days"], "time": row["time"], "room": row["room"],
                    "instructor": row["instructor"], "seats": row["seats_available"], "hours": row["credit_hours"],
                    "campus": row["programs"], "notes": row["notes"],
                })
        oldest = min(captured) if captured else None
        return {
            "snapshot_at": oldest, "check_url": snapshot.get("source_url"),
            "notice": "Cached schedule, not live. Sections and seats may have changed; verify with OBU before registering.",
            "semesters": semesters, "excluded_semesters": snapshot.get("excluded_semesters", []), "courses": courses,
        }

    def by_term(self) -> tuple[dict[str, set[str]], str | None]:
        view = self.view()
        result: dict[str, set[str]] = {s["term_id"]: set() for s in view["semesters"] if s["term_id"]}
        for code, rows in view["courses"].items():
            for row in rows:
                if row["term_id"]:
                    result[row["term_id"]].add(code)
        label = None
        if view["snapshot_at"]:
            label = "snapshot " + datetime.fromisoformat(view["snapshot_at"]).strftime("%b %d, %Y").replace(" 0", " ")
        return result, label


Handler = Callable[[Request], Awaitable[Response]]


def routes(schedule: SnapshotScheduleClient, data: PlannerData, offerings: Offerings | None = None) -> list[Route]:
    """Starlette routes for the planner API, each with CORS, OPTIONS, and JSON error handling."""
    offerings = offerings or Offerings(schedule)
    table: list[Route] = []

    def route(path: str, methods: list[str]):
        def wrap(handler: Handler) -> Handler:
            async def endpoint(request: Request) -> Response:
                if request.method == "OPTIONS":
                    return _cors(request, Response(status_code=204))
                try:
                    return _cors(request, await handler(request))
                except PermissionError as exc:
                    return _error(request, 403, str(exc))
                except OverflowError:
                    return _error(request, 413, f"Request body is larger than {MAX_BODY // 1024} KiB.")
                except PlanError as exc:
                    return _error(request, 400, str(exc))
                except (KeyError, ValueError) as exc:
                    return _error(request, 404 if isinstance(exc, KeyError) else 400,
                                  str(exc.args[0]) if exc.args else "Not found.")
                except RuntimeError as exc:
                    return _error(request, 503, str(exc))
            table.append(Route(PREFIX + path, endpoint, methods=[*methods, "OPTIONS"]))
            return handler
        return wrap

    @route("/catalogs", ["GET"])
    async def catalogs(request: Request) -> Response:
        return JSONResponse(await asyncio.to_thread(data.catalogs))

    def cached(request: Request, raw: bytes, etag: str) -> Response:
        headers = {"ETag": f'"{etag}"', "Cache-Control": "public, max-age=3600"}
        if request.headers.get("if-none-match") == f'"{etag}"':
            return Response(status_code=304, headers=headers)
        return Response(raw, media_type="application/json", headers=headers)

    def no_year(year: str) -> Response:
        return JSONResponse({"error": f"No planner data for catalog year {year!r}."}, status_code=404)

    @route("/{year}/bundle", ["GET"])
    async def bundle(request: Request) -> Response:
        year = request.path_params["year"]
        try:
            raw, etag = await asyncio.to_thread(data.raw, year)
        except KeyError:
            return no_year(year)
        return cached(request, raw, etag)

    @route("/{year}/index", ["GET"])
    async def course_index(request: Request) -> Response:
        year = request.path_params["year"]
        try:
            raw, etag = await asyncio.to_thread(data.index, year)
        except KeyError:
            return no_year(year)
        return cached(request, raw, etag)

    @route("/{year}/courses/{code}", ["GET"])
    async def course_detail(request: Request) -> Response:
        year, code = request.path_params["year"], re.sub(r"\s+", " ", request.path_params["code"]).strip().upper()
        try:
            bundle_data = await asyncio.to_thread(data.bundle, year)
        except KeyError:
            return no_year(year)
        course = bundle_data["_index"].get(code)
        if course is None:
            return JSONResponse({"error": f"{code} is not in the {year} catalog."}, status_code=404)
        return JSONResponse(course, headers={"Cache-Control": "public, max-age=3600"})

    @route("/offerings", ["GET"])
    async def get_offerings(request: Request) -> Response:
        return JSONResponse(await asyncio.to_thread(offerings.view), headers={"Cache-Control": "public, max-age=600"})

    @route("/check", ["POST"])
    async def check(request: Request) -> Response:
        plan = await read_json(request)

        def run() -> dict:
            try:
                by_term, label = offerings.by_term()
            except RuntimeError:
                by_term, label = None, None
            return check_plan(plan, data, offerings=by_term, snapshot_label=label)
        return JSONResponse(await asyncio.to_thread(run))

    @route("/worksheet", ["POST"])
    async def worksheet(request: Request) -> Response:
        body = await read_json(request)
        if not isinstance(body, dict) or not isinstance(body.get("sequences"), list):
            raise PlanError("Send {\"semester\": ..., \"sequences\": [...]}.")
        name, student_id = str(body.get("student_name") or "")[:80], str(body.get("student_id") or "")[:20]
        edit_url = body.get("edit_url") or ""
        # Only the planner's own schedule link may become the printed QR code.
        if not isinstance(edit_url, str) or (edit_url and (
                len(edit_url) > 2000 or not EDIT_URL.fullmatch(edit_url))):
            raise PlanError("edit_url must be this planner's schedule link.")
        custom = custom_rows(body.get("custom"))
        if not body["sequences"] and not custom:
            raise PlanError("Pick at least one section or add a custom class.")
        if len(body["sequences"]) + len(custom) > 12:
            raise PlanError("The worksheet holds at most 12 classes.")

        year = body.get("catalog_year")

        def run() -> dict:
            sections = schedule.sections([str(s) for s in body["sequences"]], body.get("semester"))
            # Catalog titles ("University Physics I") like the website, instead of the schedule's
            # abbreviations ("UNIV PHYSICS I"); copies, since snapshot records are shared.
            try:
                index = data.bundle(year)["_index"] if isinstance(year, str) else {}
            except KeyError:
                index = {}
            titled = [{**c, "title": index[course_label(c)]["title"]} if course_label(c) in index else c
                      for c in sections["courses"]]
            sections["courses"] = [*titled, *custom]
            result = render_worksheet(sections, name.strip(), student_id.strip(), edit_url)
            return {"html": result["html"], "filename": result["suggested_filename"],
                    "conflicts": result["conflicts"], "total_credit_hours": result["total_credit_hours"]}
        return JSONResponse(await asyncio.to_thread(run))

    return table
