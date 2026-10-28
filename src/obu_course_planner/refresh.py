"""Capture every semester/program listing (no per-filter queries), then publish atomically.

Filters (school, time slot, term, level) and credit hours are derived from the listing
itself, so the request count is about one per page plus one per semester/program.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

import httpx

from .schedule import SOURCE_URL, ScheduleClient
from .snapshot import SCHEMA_VERSION, SNAPSHOT_PATH, SnapshotScheduleClient, course_id


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".json", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o640)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# Day ranges in which a term is in progress, from the 2026-27 residential catalog calendar.
# (start month, day, year offset, end month, day): Winter YYYY starts in December of YYYY-1.
TERM_WINDOWS = {
    "fall": (8, 1, 0, 12, 20), "winter": (12, 21, -1, 1, 10), "spring": (1, 11, 0, 5, 9),
    "may": (5, 10, 0, 7, 31), "summer": (5, 10, 0, 7, 31),
}


def is_current(label: str, today: date) -> bool:
    """True when today falls in the labelled term ('Fall 2026'); unrecognised labels are kept."""
    match = re.fullmatch(r"\s*([A-Za-z]+)(?:\s+Term)?\s+(\d{4})\s*", label)
    window = TERM_WINDOWS.get(match[1].casefold()) if match else None
    if window is None:
        return False
    start_month, start_day, offset, end_month, end_day = window
    year = int(match[2])
    return date(year + offset, start_month, start_day) <= today <= date(year, end_month, end_day)


def capture(delay: float = 1.0, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        source = ScheduleClient(http, request_delay=delay)
        _, form, initial = source.open()
        # The semester in progress is skipped: registration for it is over, and skipping it
        # roughly halves the requests. Only upcoming semesters are captured.
        semesters = [s for s in initial["semester"] if not is_current(s["label"], today)]
        excluded = [{**s, "reason": "current semester"} for s in initial["semester"] if is_current(s["label"], today)]
        if not semesters:
            raise RuntimeError("Every listed semester is in progress; nothing upcoming to capture.")
        site_default = form["SEM_filt"].strip()
        result = {"schema_version": SCHEMA_VERSION, "complete": False, "started_at": now(),
                  "source_url": SOURCE_URL, "default_semester": site_default,
                  "semesters": semesters, "excluded_semesters": excluded, "datasets": {}}
        programs = [o["value"] for o in initial["campus"] if o["value"]]
        if not programs:
            raise RuntimeError("OBU listed no programs; snapshot not replaced.")
        first_program = "RES" if "RES" in programs else programs[0]
        for semester in semesters:
            sem = semester["value"]
            # OBU's all-programs listing resets to Residential on page 2 (verified: it returns only
            # the RES rows), so each program is listed separately. The first program's response
            # carries this semester's own program list, so no separate all-programs request is made.
            dataset = {"snapshot_at": now(), "pages": 0, "options_by_campus": {}}
            records: dict[str, dict] = {}
            queue, seen = [first_program], set()
            while queue:
                campus = queue.pop(0)
                seen.add(campus)
                soup, form, options = source.select(form, sem, campus)
                if campus == first_program:
                    dataset["options_by_campus"][""] = {**options, "campus": [{**o, "selected": not o["value"]} for o in options["campus"]]}
                    queue += [o["value"] for o in options["campus"] if o["value"] and o["value"] not in seen]
                courses, pages = source.collect(soup, form)
                dataset["pages"] += pages
                dataset["options_by_campus"][campus] = options
                for course in courses:
                    record = records.setdefault(course_id(course), {**course, "programs": []})
                    record["programs"].append(campus)
                print(f"{sem}/{campus}: {len(courses)} sections / {pages} pages", flush=True)
            listed = {o["value"] for o in dataset["options_by_campus"][""]["campus"] if o["value"]}
            if first_program not in listed:
                raise RuntimeError(f"{first_program} is not offered in {semester['label']}; snapshot not replaced.")
            # All-programs choices = union of each program's choices (same order, first label wins).
            for key in ("department", "school", "time_slot", "term", "course_level", "course_hours"):
                merged = {}
                for options in dataset["options_by_campus"].values():
                    for option in options[key]:
                        merged.setdefault(option["value"], {**option, "selected": not option["value"]})
                dataset["options_by_campus"][""][key] = list(merged.values())
            dataset["courses"] = list(records.values())
            result["datasets"][sem] = dataset
        if site_default not in result["datasets"]:
            # Site default is the skipped current semester: default to the upcoming semester
            # with the most sections (e.g. Spring rather than a small online-only Winter term).
            result["default_semester"] = max(result["datasets"], key=lambda s: len(result["datasets"][s]["courses"]))
        result.update(complete=True, completed_at=now(), request_count=source.requests)
        return result


def refresh(output: Path, delay: float = 1.0) -> dict:
    """Failure updates status only; the last complete data file stays untouched."""
    status_path = output.parent / "refresh-status.json"
    started = now()
    try:
        snapshot = capture(delay)
        # Validate the complete candidate before replacing a working snapshot.
        with tempfile.TemporaryDirectory(dir=output.parent if output.parent.exists() else None) as directory:
            candidate = Path(directory) / "candidate.json"
            atomic_json(candidate, snapshot)
            SnapshotScheduleClient(candidate)._load()
        atomic_json(output, snapshot)
    except Exception as exc:
        atomic_json(status_path, {"attempted_at": started, "finished_at": now(), "success": False,
                                  "error": str(exc), "source_url": SOURCE_URL,
                                  "previous_snapshot_preserved": output.exists()})
        raise
    status = {"attempted_at": started, "finished_at": now(), "success": True,
              "snapshot_completed_at": snapshot["completed_at"], "source_url": SOURCE_URL,
              "request_count": snapshot["request_count"],
              "semester_count": len(snapshot["datasets"]),
              "section_count": sum(len(d["courses"]) for d in snapshot["datasets"].values())}
    atomic_json(status_path, status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between HTTP requests (sequential, no retries)")
    args = parser.parse_args()
    if not 0.1 <= args.delay <= 10:
        parser.error("--delay must be between 0.1 and 10 seconds")
    print(json.dumps(refresh(args.output, args.delay), indent=2))


if __name__ == "__main__":
    main()
