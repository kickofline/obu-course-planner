"""Offline section lookups over complete schedule snapshots with refresh provenance."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock

from .schedule import SOURCE_URL, choose

SNAPSHOT_PATH = Path(__file__).parent / "data" / "schedule-snapshot.json"
SCHEMA_VERSION = 2


def course_id(course: dict) -> str:
    return "|".join(course[key] for key in ("sequence", "department", "course"))


class SnapshotScheduleClient:
    def __init__(self, path: Path = SNAPSHOT_PATH) -> None:
        self._path = Path(path)
        self._snapshot: dict | None = None
        self._fingerprint: tuple[int, int] | None = None
        self._lock = RLock()

    def _load(self) -> dict:
        with self._lock:
            try:
                stat = self._path.stat()
            except OSError as exc:
                raise RuntimeError("Schedule snapshot is unavailable; refresh it before serving queries.") from exc
            fingerprint = (stat.st_mtime_ns, stat.st_size)
            if fingerprint != self._fingerprint:
                self._snapshot = self._read()
                self._fingerprint = fingerprint
            return self._snapshot

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data["schema_version"] != SCHEMA_VERSION or data["complete"] is not True:
                raise ValueError("snapshot is not complete or uses an unsupported schema")
            if not data["semesters"] or set(data["datasets"]) != {s["value"] for s in data["semesters"]}:
                raise ValueError("snapshot semester coverage is incomplete")
            for dataset in data["datasets"].values():
                programs = {o["value"] for o in dataset["options_by_campus"][""]["campus"] if o["value"]}
                if set(dataset["options_by_campus"]) != {"", *programs}:
                    raise ValueError("snapshot program coverage is incomplete")
                if len({course_id(c) for c in dataset["courses"]}) != len(dataset["courses"]):
                    raise ValueError("duplicate snapshot section identities")
                if any(not c["programs"] or not set(c["programs"]) <= programs for c in dataset["courses"]):
                    raise ValueError("snapshot section has an unknown program")
                datetime.fromisoformat(dataset["snapshot_at"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Schedule snapshot is unavailable or incomplete; a complete refresh is required.") from exc
        return data

    def _select(self, semester: str | None, campus: str) -> tuple[dict, dict, str, str]:
        snapshot = self._load()
        if semester is not None:
            for skipped in snapshot.get("excluded_semesters", []):
                if semester.strip().casefold() in (skipped["value"].casefold(), skipped["label"].casefold()):
                    raise ValueError(f"{skipped['label']} is the current semester and is not cached; check "
                                     f"{SOURCE_URL} directly for its sections.")
        sem = snapshot["default_semester"] if semester is None else choose({"semester": snapshot["semesters"]}, "semester", semester)
        dataset = snapshot["datasets"][sem]
        loc = choose(dataset["options_by_campus"][""], "campus", campus)
        return snapshot, dataset, sem, loc

    def _provenance(self, snapshot: dict, dataset: dict) -> dict:
        captured = datetime.fromisoformat(dataset["snapshot_at"])
        now = datetime.now(timezone.utc)
        try:
            refresh_status = json.loads((self._path.parent / "refresh-status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            refresh_status = {"success": None, "error": "Refresh status is unavailable"}
        return {
            "source_url": SOURCE_URL, "data_mode": "snapshot", "is_live": False,
            "snapshot_at": dataset["snapshot_at"],
            "snapshot_completed_at": snapshot["completed_at"],
            "snapshot_age_seconds": max(0, int((now - captured).total_seconds())),
            "served_at": now.isoformat(),
            "refresh_policy": refresh_status.get("refresh_policy", "scheduled refresh; last complete snapshot retained on failure"),
            "last_refresh": refresh_status,
            "notice": "Cached data may be out of date. Check the source URL for current offerings and enrollment.",
            "check_url": SOURCE_URL,
        }

    def sections(self, sequences: list[str], semester: str | None = None) -> dict:
        """Exact lookup by OBU sequence number across every captured program. An empty list returns just the
        semester and provenance (worksheets made only of custom classes)."""
        cleaned = [str(s).strip() for s in sequences]
        if len(cleaned) > 12:
            raise ValueError("Provide at most 12 section sequence numbers.")
        if any(not s.isdigit() for s in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("Sequence numbers must be unique digit strings, e.g. '993'.")
        snapshot, dataset, sem, _ = self._select(semester, "")
        by_sequence: dict[str, list[dict]] = {}
        for course in dataset["courses"]:
            by_sequence.setdefault(course["sequence"], []).append(course)
        missing = [s for s in cleaned if s not in by_sequence]
        if missing:
            raise ValueError(f"Sequence number(s) {', '.join(missing)} not found in the cached {sem} schedule.")
        ambiguous = [s for s in cleaned if len(by_sequence[s]) > 1]
        if ambiguous:
            raise ValueError(f"Sequence number(s) {', '.join(ambiguous)} match more than one section; cannot choose safely.")
        label = next(o["label"] for o in snapshot["semesters"] if o["value"] == sem)
        return {**self._provenance(snapshot, dataset), "semester": sem, "semester_label": label,
                "courses": [by_sequence[s][0] for s in cleaned]}
