import copy
from datetime import date
from urllib.parse import parse_qs

import httpx
import pytest

import test_schedule as fixtures
from obu_course_planner import refresh
from obu_course_planner.snapshot import SnapshotScheduleClient

SCIENCE, BUSINESS = "Patterson School of Natural Sciences", "Hickingbotham School of Business"


def section(seq, **extra):
    base = {"sequence": seq, "department": "CYBR", "course": "3023.0", "course_code": "CYBR 3023.0",
            "title": "FORENSICS", "credit_hours": 3.0, "school": SCIENCE, "seats_available": 2,
            "time_slot": "G", "term": "Spring Semester", "days": "MWF", "time": "11:00 - 11:50",
            "room": "JSC343", "instructor": "Anderson, Scott", "notes": [], "programs": ["RES"]}
    return {**base, **extra}


def snapshot_data():
    choices = {"semester": [("227", "Spring 2027")], "campus": [("", ""), ("RES", "Residential"), ("ONL", "Online")],
               "department": [("", ""), ("CYBR", "Cybersecurity"), ("ACCT", "Accounting")],
               "school": [("", ""), ("I", SCIENCE), ("C", BUSINESS)],
               "time_slot": [("", ""), ("A", "A - 8:00 MWF"), ("G", "G - 11:00 MWF")],
               "term": [("", ""), ("A", "Spring Semester"), ("B", "Spring Term 1")],
               "course_level": [("", ""), ("2", "Sophomore"), ("3", "Junior")],
               "course_hours": [("", "")]}
    options = {k: [{"value": v, "label": l, "selected": not v} for v, l in vals] for k, vals in choices.items()}
    courses = [
        section("1"),
        section("2", department="ACCT", course="2013.0", course_code="ACCT 2013.0", school=BUSINESS,
                time_slot="ACEG", seats_available=0),
        section("3", time_slot="TBA", days="TBA", term="Spring Term 1", programs=["ONL"]),
    ]
    return {"schema_version": 2, "complete": True, "default_semester": "227", "semesters": options["semester"],
            "completed_at": "2026-10-27T12:10:00+00:00", "datasets": {"227": {
                "snapshot_at": "2026-10-27T12:00:00+00:00", "courses": courses,
                "options_by_campus": {k: copy.deepcopy(options) for k in ("", "RES", "ONL")}}}}


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "snapshot.json"
    refresh.atomic_json(path, snapshot_data())
    return SnapshotScheduleClient(path)


def seqs(result):
    return [row["sequence"] for row in result["courses"]]


def captured(store, campus=None):
    courses = store._load()["datasets"]["227"]["courses"]
    return sorted(c["sequence"] for c in courses if campus is None or campus in c["programs"])


def test_source_age_is_not_response_time_and_failed_refresh_is_visible(tmp_path, store):
    refresh.atomic_json(tmp_path / "refresh-status.json", {"success": False, "error": "HTTP 403"})
    result = store.sections(["1"])
    assert result["is_live"] is False and result["snapshot_at"] == "2026-10-27T12:00:00+00:00"
    assert result["served_at"] != result["snapshot_at"] and result["last_refresh"]["success"] is False


def test_atomic_refresh_is_visible_without_restarting_the_server(tmp_path, store, monkeypatch):
    replacement = snapshot_data()
    replacement["datasets"]["227"]["courses"][0]["title"] = "NEW REPLACEMENT OFFERING"
    replacement["request_count"] = 9
    assert store.sections(["1"])["courses"][0]["title"] != "NEW REPLACEMENT OFFERING"
    monkeypatch.setattr(refresh, "capture", lambda delay: replacement)
    refresh.refresh(tmp_path / "snapshot.json")
    assert store.sections(["1"])["courses"][0]["title"] == "NEW REPLACEMENT OFFERING"


def test_failed_or_incomplete_capture_preserves_last_complete_snapshot(tmp_path, store, monkeypatch):
    path = tmp_path / "snapshot.json"
    original = path.read_bytes()
    def blocked(delay):
        raise RuntimeError("OBU schedule request failed (HTTP 403)")
    monkeypatch.setattr(refresh, "capture", blocked)
    with pytest.raises(RuntimeError):
        refresh.refresh(path)
    assert path.read_bytes() == original and store.sections(["1"])["last_refresh"]["success"] is False
    incomplete = snapshot_data()
    incomplete["datasets"]["227"]["courses"][0]["programs"] = []
    monkeypatch.setattr(refresh, "capture", lambda delay: incomplete)
    with pytest.raises(RuntimeError):
        refresh.refresh(path)
    assert path.read_bytes() == original


def test_old_schema_snapshot_is_rejected_not_misread(tmp_path):
    data = snapshot_data()
    data["schema_version"] = 1
    refresh.atomic_json(tmp_path / "old.json", data)
    with pytest.raises(RuntimeError, match="incomplete"):
        SnapshotScheduleClient(tmp_path / "old.json").sections(["1"])


def test_section_lookup_keeps_order_and_rejects_unknown(store):
    result = store.sections(["3", "1"])
    assert seqs(result) == ["3", "1"] and result["semester_label"] == "Spring 2027"
    assert store.sections([])["courses"] == []  # custom-class-only worksheets still get the semester
    with pytest.raises(ValueError, match="999"):
        store.sections(["1", "999"])
    with pytest.raises(ValueError):
        store.sections(["1"], semester="999")


@pytest.mark.parametrize("sequences", [["x"], ["1", "1"], [str(n) for n in range(13)]])
def test_invalid_sequences_are_rejected_before_loading_snapshot(tmp_path, sequences):
    with pytest.raises(ValueError):
        SnapshotScheduleClient(tmp_path / "missing.json").sections(sequences)


def test_capture_uses_listing_pages_only_and_merges_programs(tmp_path, monkeypatch):
    choices = {name: [""] for name in fixtures.CHOICES}
    choices.update(SEM_filt=["227"], MLLOC_filt=["", "RES", "ONL"], CLDPCR_filt=["", "CYBR"])
    monkeypatch.setattr(fixtures, "CHOICES", choices)
    state = {"SEM_filt": "227", "MLLOC_filt": "RES"}
    log = []
    def handler(request):
        if request.method == "POST":
            state.update({k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()})
        page_number = request.url.params.get("page")
        log.append((request.method, state["MLLOC_filt"], page_number))
        campus = state["MLLOC_filt"]
        if campus == "RES":
            rows = fixtures.course(1) if page_number is None else fixtures.course(4, number="1000.5")
            return httpx.Response(200, text=fixtures.page(state, rows, count=100 if page_number is None else 1,
                                                          heading="Patterson School of Natural Sciences" if page_number is None else None))
        rows = fixtures.course(2) if campus == "ONL" else fixtures.course(1)
        return httpx.Response(200, text=fixtures.page(state, rows, count=1))
    original = httpx.Client
    monkeypatch.setattr(refresh.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    data = refresh.capture(delay=0)
    # 1 initial GET + per semester: 1 POST per program + extra pages; no all-programs request.
    assert data["request_count"] == len(log) == 4
    assert all(campus for method, campus, _ in log if method == "POST")
    refresh.atomic_json(tmp_path / "s.json", data)
    store = SnapshotScheduleClient(tmp_path / "s.json")
    assert captured(store) == ["1", "2", "4"]
    assert captured(store, "ONL") == ["2"]
    assert store.sections(["4"])["courses"][0]["credit_hours"] == 0.5


@pytest.mark.parametrize("label,today,current", [
    ("Fall 2026", date(2026, 10, 27), True), ("Fall 2026", date(2026, 12, 21), False),
    ("Winter 2027", date(2026, 12, 21), True), ("Winter 2027", date(2026, 10, 27), False),
    ("Spring 2027", date(2027, 1, 11), True), ("Spring 2027", date(2026, 10, 27), False),
    ("Summer 2027", date(2027, 6, 1), True), ("Fall Term 1 2026", date(2026, 9, 1), False),
])
def test_current_semester_by_date(label, today, current):
    assert refresh.is_current(label, today) is current


def test_current_semester_is_never_requested_and_explained(tmp_path, monkeypatch):
    choices = {name: [""] for name in fixtures.CHOICES}
    choices.update(SEM_filt=["126", "227"], MLLOC_filt=["", "RES"])
    monkeypatch.setattr(fixtures, "CHOICES", choices)
    monkeypatch.setattr(fixtures, "LABELS", {"126": "Fall 2026", "227": "Spring 2027"})
    state = {"SEM_filt": "126", "MLLOC_filt": "RES"}
    posted = []
    def handler(request):
        if request.method == "POST":
            state.update({k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()})
            posted.append(state["SEM_filt"])
        return httpx.Response(200, text=fixtures.page(state, fixtures.course(1), count=1))
    original = httpx.Client
    monkeypatch.setattr(refresh.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    data = refresh.capture(delay=0, today=date(2026, 10, 27))
    assert set(posted) == {"227"} and data["request_count"] == 2
    assert list(data["datasets"]) == ["227"] and data["default_semester"] == "227"
    refresh.atomic_json(tmp_path / "s.json", data)
    store = SnapshotScheduleClient(tmp_path / "s.json")
    assert store.sections([])["semester"] == "227"
    assert [s["value"] for s in store._load()["semesters"]] == ["227"]
    with pytest.raises(ValueError, match="current semester"):
        store.sections([], semester="Fall 2026")
