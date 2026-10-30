import json

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from test_planner import COURSES, PROGRAMS, RULES, course
from test_snapshot import snapshot_data
from obu_course_planner import refresh
from obu_course_planner.planner import PlannerData
from obu_course_planner.planner_api import MAX_BODY, Offerings, routes
from obu_course_planner.snapshot import SnapshotScheduleClient

SITE = "https://planner.example"
# ACCT 2013 is on the cached schedule (seq 2, as "ACCT 2013.0") and in this catalog under its full title.
API_COURSES = [*COURSES, course("ACCT 2013", "Principles of Accounting II")]
PLAN = {"catalog_year": "2026-27", "programs": ["core", "major-cyber"],
        "terms": [{"id": "2027SP", "courses": [{"code": "CYBR 3023"}, {"code": "CYBR 2013"}]}]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    bundle = {"catalog_year": "2026-27", "catalog_sha256": "x", "rules": RULES, "courses": API_COURSES,
              "programs": PROGRAMS}
    (tmp_path / "2026-27.json").write_text(json.dumps(bundle), encoding="utf-8")
    refresh.atomic_json(tmp_path / "snapshot.json", snapshot_data())
    schedule = SnapshotScheduleClient(tmp_path / "snapshot.json")
    monkeypatch.setenv("PLANNER_ALLOWED_ORIGINS", f"{SITE}, https://www.planner.example/")
    monkeypatch.setenv("PLANNER_SITE_URL", f"{SITE}/courses")
    app = Starlette(routes=routes(schedule, PlannerData(tmp_path), Offerings(schedule)))
    with TestClient(app) as client:
        yield (client,)



def test_catalogs_bundle_and_etag(env):
    client, *_ = env
    listing = client.get("/api/planner/v1/catalogs").json()
    assert [p["id"] for p in listing["years"][0]["programs"]] == ["core", "major-cyber", "minor-history"]
    first = client.get("/api/planner/v1/2026-27/bundle")
    assert first.status_code == 200 and len(first.json()["courses"]) == len(API_COURSES)
    again = client.get("/api/planner/v1/2026-27/bundle", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert client.get("/api/planner/v1/1999-00/bundle").status_code == 404


def test_index_is_slim_and_details_load_per_course(env):
    client, *_ = env
    first = client.get("/api/planner/v1/2026-27/index")
    courses = first.json()["courses"]
    assert len(courses) == len(API_COURSES) and "description" not in courses[0] and courses[0]["title"]
    assert client.get("/api/planner/v1/2026-27/index", headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    detail = client.get("/api/planner/v1/2026-27/courses/cybr%201003")
    assert detail.status_code == 200 and detail.json()["description"]
    assert client.get("/api/planner/v1/2026-27/courses/ZZZZ%209999").status_code == 404
    assert client.get("/api/planner/v1/1999-00/index").status_code == 404


def test_offerings_map_sections_to_term_ids(env):
    client, *_ = env
    view = client.get("/api/planner/v1/offerings").json()
    assert view["semesters"][0]["term_id"] == "2027SP"
    assert {row["seq"] for row in view["courses"]["CYBR 3023"]} == {"1", "3"}
    assert view["courses"]["ACCT 2013"][0]["seats"] == 0


def test_check_uses_cached_schedule(env):
    client, *_ = env
    result = client.post("/api/planner/v1/check", json=PLAN, headers={"Origin": SITE})
    assert result.headers["access-control-allow-origin"] == SITE
    courses = {c["code"]: c for c in result.json()["terms"][0]["courses"]}
    assert not courses["CYBR 3023"]["known"]
    assert any(w["kind"] == "schedule" for w in courses["CYBR 2013"]["warnings"])


# Configured origins replace the localhost defaults; trailing slashes in config are ignored.
@pytest.mark.parametrize("origin,allowed", [(SITE, True), ("https://www.planner.example", True),
                                            ("http://localhost:4321", False),
                                            ("https://planner.example.evil.example", False), ("https://evil.example", False)])
def test_cors_preflight_and_origin_enforcement(env, origin, allowed):
    client, *_ = env
    preflight = client.options("/api/planner/v1/check", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert preflight.status_code == 204
    assert (preflight.headers.get("access-control-allow-origin") == origin) is allowed
    post = client.post("/api/planner/v1/check", json=PLAN, headers={"Origin": origin})
    assert post.status_code == (200 if allowed else 403)


def test_bad_plan_and_oversized_body(env):
    client, *_ = env
    assert client.post("/api/planner/v1/check", json={"catalog_year": "1999-00"}).status_code == 400
    assert client.post("/api/planner/v1/check", content=b"{not json").status_code == 400
    huge = client.post("/api/planner/v1/check", content=b" " * (MAX_BODY + 1),
                       headers={"Content-Type": "application/json"})
    assert huge.status_code == 413


def test_worksheet_endpoint(env):
    client, *_ = env
    sheet = client.post("/api/planner/v1/worksheet", json={"semester": "227", "sequences": ["1"],
                                                           "student_name": "<b>X</b>"})
    body = sheet.json()
    assert sheet.status_code == 200 and body["filename"] == "pre-registration-spring-2027.html"
    assert "&lt;b&gt;X&lt;/b&gt;" in body["html"]
    assert '<a href="https://planner.example/courses">planner.example/courses</a>' in body["html"] and "<svg" not in body["html"]
    assert client.post("/api/planner/v1/worksheet", json={"sequences": ["999"]}).status_code == 400


def test_worksheet_edit_qr(env):
    client, *_ = env
    url = "/api/planner/v1/worksheet"
    edit = "https://catalog.example/courses/#plan=cAbC_12-x"
    html = client.post(url, json={"semester": "227", "sequences": ["1"], "edit_url": edit}).json()["html"]
    qr = html[html.index('<div class="qr">'):]
    # Bottom-right card: caption, QR, then the planner URL directly under it; footer text comes first.
    assert html.index("Schedule updated") < html.index('<div class="qr">')
    assert qr.index("Scan to edit") < qr.index(f'<a class="code" href="{edit}"><svg') < qr.index(
        '<a href="https://planner.example/courses">planner.example/courses</a>')
    for bad in ["javascript:alert(1)", "https://evil.example/phish", "https://catalog.example/courses/#plan=c<x>",
                edit + "a" * 2000, 7]:
        response = client.post(url, json={"semester": "227", "sequences": ["1"], "edit_url": bad})
        assert response.status_code == 400, bad


def test_worksheet_custom_classes(env):
    client, *_ = env
    url = "/api/planner/v1/worksheet"
    custom = {"code": "math 1003", "title": "Arranged <Algebra>", "hours": 3, "days": "TTH", "start": 450, "end": 525}
    body = client.post(url, json={"semester": "227", "sequences": ["1"], "custom": [custom]}).json()
    assert "MATH 1003" in body["html"] and "Arranged &lt;Algebra&gt;" in body["html"]
    assert "7:30a–8:45a" in body["html"]  # exact minutes: OBU's 1-7 = p.m. rule must not move it
    titled = client.post(url, json={"semester": "227", "sequences": ["2"], "catalog_year": "2026-27"}).json()["html"]
    assert "Principles of Accounting II" in titled
    untitled = client.post(url, json={"semester": "227", "sequences": ["2"]}).json()["html"]
    assert "Principles of Accounting II" not in untitled  # no catalog year sent: schedule title stays
    only_custom = client.post(url, json={"semester": "227", "sequences": [], "custom": [{"code": "MUAP 1131"}]})
    assert only_custom.status_code == 200 and "MUAP 1131" in only_custom.json()["html"]
    assert client.post(url, json={"semester": "227", "sequences": []}).status_code == 400
    bad_time = {**custom, "end": 400}
    assert client.post(url, json={"semester": "227", "sequences": ["1"], "custom": [bad_time]}).status_code == 400
