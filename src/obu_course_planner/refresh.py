"""Capture every semester/program listing (no per-filter queries), then publish atomically.

Filters (school, time slot, term, level) and credit hours are derived from the listing.
The persistent native session paces every HTTP(S) request, including challenge traffic and redirects.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .cfsolve_transport import CfsolveSession

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
    with CfsolveSession(delay=delay) as session:
        source = ScheduleClient(session)
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
    result.update(complete=True, completed_at=now(), request_count=session.requests)
    return result


def section_delta(previous: dict | None, snapshot: dict) -> dict:
    """Compare section content, not capture timestamps, listing order, or option defaults."""
    def rows(data):
        return {(sem, course_id(row)): row for sem, dataset in data["datasets"].items()
                for row in dataset["courses"]}

    before = rows(previous) if previous else {}
    after = rows(snapshot)
    added, removed, changed = [], [], []
    if previous is not None:
        for sem, identity in sorted(after.keys() - before.keys()):
            added.append({"semester": sem, "section": after[sem, identity]})
        for sem, identity in sorted(before.keys() - after.keys()):
            removed.append({"semester": sem, "section": before[sem, identity]})
        for key in sorted(before.keys() & after.keys()):
            old, new = before[key], after[key]
            fields = {}
            for field in sorted(old.keys() | new.keys()):
                left, right = old.get(field), new.get(field)
                if field == "programs":
                    left, right = sorted(left or []), sorted(right or [])
                if left != right or (field in old) != (field in new):
                    fields[field] = {"before": left, "after": right}
            if fields:
                changed.append({"semester": key[0], "section_id": key[1], "fields": fields})
    return {
        "baseline": previous is None,
        "from_completed_at": previous["completed_at"] if previous else None,
        "to_completed_at": snapshot["completed_at"],
        "semesters_before": previous["semesters"] if previous else [],
        "semesters_after": snapshot["semesters"],
        "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)},
        "added": added, "removed": removed, "changed": changed,
    }


def history(output: Path):
    """Read only the committed history chain, newest first; never contact OBU."""
    snapshot = SnapshotScheduleClient(output)._load()
    identity = snapshot.get("history_id")
    seen = set()
    while identity is not None:
        if not re.fullmatch(r"[0-9a-f]{32}", identity) or identity in seen:
            raise RuntimeError("Invalid schedule history chain.")
        seen.add(identity)
        record = json.loads((output.parent / "history" / f"{identity}.json").read_text(encoding="utf-8"))
        yield record
        identity = record["previous_id"]


def refresh(output: Path, delay: float = 1.0) -> dict:
    """Failure updates status only; the last complete data file stays untouched."""
    status_path = output.parent / "refresh-status.json"
    started = now()
    try:
        snapshot = capture(delay)
        previous = SnapshotScheduleClient(output)._load() if output.exists() else None
        delta = section_delta(previous, snapshot)
        # Validate the complete candidate before replacing a working snapshot.
        with tempfile.TemporaryDirectory(dir=output.parent if output.parent.exists() else None) as directory:
            candidate = Path(directory) / "candidate.json"
            atomic_json(candidate, snapshot)
            SnapshotScheduleClient(candidate)._load()
        # Immutable records are prepared first. The atomic snapshot replacement commits the
        # history head and the data together; interrupted, unreferenced records are not history.
        archive = output.parent / "history"
        archive.mkdir(parents=True, exist_ok=True)
        os.chmod(archive, 0o750)
        previous_id = previous.get("history_id") if previous else None
        if previous is not None and previous_id is None:
            previous_id = uuid4().hex
            atomic_json(archive / f"{previous_id}.json", {
                "id": previous_id, "previous_id": None, "kind": "baseline",
                "snapshot_completed_at": previous["completed_at"],
                "section_count": sum(len(d["courses"]) for d in previous["datasets"].values()),
            })
        identity = uuid4().hex
        atomic_json(archive / f"{identity}.json", {
            "id": identity, "previous_id": previous_id, "kind": "capture",
            "attempted_at": started, "snapshot_completed_at": snapshot["completed_at"],
            "request_count": snapshot["request_count"], "delta": delta,
        })
        if os.name == "posix":
            # Persist record names before publishing a snapshot that references them.
            for directory in (archive, output.parent):
                descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        snapshot["history_id"] = identity
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
              "section_count": sum(len(d["courses"]) for d in snapshot["datasets"].values()),
              "history_id": identity, "delta_counts": delta["counts"],
              "refresh_policy": "hourly refresh; last complete snapshot retained on failure"}
    atomic_json(status_path, status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds between outbound requests (0.1-10; default: 1; no retries)")
    parser.add_argument("--history", action="store_true", help="Print committed capture deltas as JSON Lines, newest first; no network requests")
    args = parser.parse_args()
    if args.history:
        for record in history(args.output):
            print(json.dumps(record, ensure_ascii=False))
        return
    if not 0.1 <= args.delay <= 10:
        parser.error("--delay must be between 0.1 and 10 seconds")
    print(json.dumps(refresh(args.output, args.delay), indent=2))


if __name__ == "__main__":
    main()
