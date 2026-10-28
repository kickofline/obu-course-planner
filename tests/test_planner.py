from datetime import date
import json

import pytest

from obu_course_planner.planner import PlanError, PlannerData, check_plan, term_for_date


def course(code, title, prereq=None, coreq=None, standing=None, terms=("fall", "spring"), years=None,
           on_demand=False, repeatable=False, cross=(), hours=None):
    subject, number = code.split()
    last = int(number[-1]) if hours is None else hours
    return {"code": code, "subject": subject, "subject_name": subject, "number": number, "range_end": None,
            "title": title, "hours_min": last, "hours_max": last, "level": int(number[0]) * 1000,
            "description": title, "prereq_text": None, "prereq": prereq, "standing": standing, "prereq_other": [],
            "coreq_text": None, "coreq": coreq, "offered_text": None,
            "offered": {"terms": list(terms), "years": years, "on_demand": on_demand},
            "repeatable": repeatable, "cross_listed": list(cross), "page": 1, "catalog_year": "2026-27"}


COURSES = [
    course("CYBR 1003", "Introduction to Cybersecurity", terms=("fall",)),
    course("CYBR 2013", "Internet Technologies", prereq={"any": ["CYBR 1003", "CSCI 1053"]}, terms=("spring",)),
    course("CYBR 3013", "System Administration", prereq="CYBR 2013", terms=("fall",)),
    course("CYBR 4023", "Network Security", prereq="CYBR 2013", standing="junior", terms=("fall",), years="even"),
    course("CYBR 4803", "Internship", terms=(), on_demand=True),
    course("CYBR 4903", "Project", terms=(), on_demand=True),
    course("CSCI 1044", "Programming I"),
    course("CSCI 1053", "Programming II", prereq="CSCI 1044"),
    course("CSCI 3053", "Networks", coreq="CSCI 3054"),
    course("CSCI 3054", "Networks Lab"),
    course("CSCI 4993", "Special Topics", repeatable=True),
    course("MATH 1103", "Cryptology"),
    course("HIST 3013", "Shared History", cross=("PSCI 3013",)),
    course("PSCI 3013", "Shared Politics", cross=("HIST 3013",)),
    course("PEAC 1001", "Activity"),
]
PROGRAMS = [
    {"id": "core", "kind": "core", "name": "Core", "pages": [1], "verified": False, "verified_source": None,
     "requirements": [{"type": "manual", "area": "core", "label": "Chapel", "text": "Seven chapel credits"}]},
    {"id": "major-cyber", "kind": "major", "name": "Cyber", "degree": "B.A.", "pages": [1], "stated_hours": 21,
     "verified": True, "verified_source": "degree sheet", "requires_minor": None,
     "requirements": [
         {"type": "all", "area": "major", "label": "Core", "items": [
             {"type": "course", "course": "CYBR 1003"}, {"type": "course", "course": "CYBR 2013"},
             {"type": "choose", "count": 1, "items": [{"type": "course", "course": "CYBR 4803"},
                                                      {"type": "course", "course": "CYBR 4903"}]}]},
         {"type": "hours", "area": "major", "label": "Electives", "hours": 9,
          "from": {"subjects": ["CYBR", "CSCI"]},
          "limits": [{"min_hours": 3, "from": {"subjects": ["CYBR", "CSCI"], "min_level": 3000}},
                     {"max_hours": 6, "from": {"subjects": ["CSCI"]}}]},
         {"type": "all", "area": "additional", "items": [{"type": "course", "course": "MATH 1103"}]},
     ]},
    {"id": "minor-history", "kind": "minor", "name": "History", "pages": [1], "verified": False,
     "verified_source": None,
     "requirements": [{"type": "course", "area": "minor", "course": "HIST 3013"}]},
]
RULES = {"total_hours": 120, "junior_senior_hours": 39, "residence_hours": 60, "major_c_or_better_hours": 24,
         "classification": {"sophomore": 30, "junior": 60, "senior": 90}, "full_time_hours": 12,
         "max_hours_per_semester": 18, "requires_minor_default": True, "chapel_credits": 7}
FALL_2026 = date(2026, 10, 1)


@pytest.fixture()
def data(tmp_path):
    bundle = {"catalog_year": "2026-27", "catalog_sha256": "x", "rules": RULES, "courses": COURSES,
              "programs": PROGRAMS}
    (tmp_path / "2026-27.json").write_text(json.dumps(bundle), encoding="utf-8")
    return PlannerData(tmp_path)


def plan(*terms, programs=("core", "major-cyber")):
    return {"version": 1, "catalog_year": "2026-27", "programs": list(programs),
            "terms": [{"id": term_id, "courses": [c if isinstance(c, dict) else {"code": c} for c in courses]}
                      for term_id, courses in terms]}


def warnings(result, term_id, code, kind=None):
    term = next(t for t in result["terms"] if t["id"] == term_id)
    entry = next(c for c in term["courses"] if c["code"] == code)
    return [w for w in entry["warnings"] if kind is None or w["kind"] == kind]


def node(result, program_id, *path):
    item = {"items": next(p for p in result["programs"] if p["id"] == program_id)["requirements"]}
    for index in path:
        item = item["items"][index]
    return item


def test_term_windows_follow_the_catalog_calendar():
    assert term_for_date(date(2026, 8, 1)) == "2026FA"
    assert term_for_date(date(2026, 12, 21)) == "2027WI"
    assert term_for_date(date(2027, 1, 10)) == "2027WI"
    assert term_for_date(date(2027, 1, 11)) == "2027SP"
    assert term_for_date(date(2027, 5, 10)) == "2027MA"
    assert term_for_date(date(2027, 6, 1)) == "2027SU"


def test_offering_season_and_year_parity(data):
    result = check_plan(plan(("TR", ["CYBR 2013"]), ("2027SP", ["CYBR 3013"]), ("2027FA", ["CYBR 4023"]),
                             ("2028FA", ["CYBR 1003"])), data, FALL_2026)
    assert "fall" in warnings(result, "2027SP", "CYBR 3013", "offered")[0]["message"]
    assert "even" in warnings(result, "2027FA", "CYBR 4023", "offered")[0]["message"]
    assert not warnings(result, "2028FA", "CYBR 1003", "offered")


def test_cached_schedule_flags_missing_sections(data):
    result = check_plan(plan(("2027SP", ["CSCI 1044", "MATH 1103"])), data, FALL_2026,
                        offerings={"2027SP": {"CSCI 1044"}}, snapshot_label="snapshot Oct 27")
    assert not warnings(result, "2027SP", "CSCI 1044", "schedule")
    assert "snapshot Oct 27" in warnings(result, "2027SP", "MATH 1103", "schedule")[0]["message"]


def test_term_load_bounds(data):
    light = check_plan(plan(("2027SP", ["CSCI 1044"])), data, FALL_2026)
    assert light["terms"][0]["warnings"][0]["kind"] == "load"
    heavy = check_plan(plan(("2027SP", [{"code": "ZZZ 1003", "hours": 12}, {"code": "ZZZ 1007", "hours": 7}])),
                       data, FALL_2026)
    assert "above 18" in heavy["terms"][0]["warnings"][0]["message"]
    summer = check_plan(plan(("2027SU", ["CSCI 1044"])), data, FALL_2026)
    assert not summer["terms"][0]["warnings"]


def test_duplicate_unless_repeatable_and_cross_listed_counts_once(data):
    result = check_plan(plan(("2027SP", ["CSCI 1044", "CSCI 4993"]), ("2027FA", ["CSCI 1044", "CSCI 4993"]),
                             ("2028SP", ["HIST 3013"]), ("2028FA", ["PSCI 3013"])), data, FALL_2026)
    assert warnings(result, "2027FA", "CSCI 1044", "duplicate")
    assert not warnings(result, "2027FA", "CSCI 4993", "duplicate")
    assert warnings(result, "2028FA", "PSCI 3013", "duplicate")
    assert result["graduation"]["total_hours"]["planned"] == 4 + 3 + 3 + 3


def test_status_done_in_progress_planned_and_past_ungraded(data):
    result = check_plan(plan(("2025FA", ["CSCI 1044"]), ("2026SP", [{"code": "MATH 1103", "grade": "B+"}]),
                             ("2026FA", ["CYBR 1003"]), ("2027SP", ["PEAC 1001"])), data, FALL_2026)
    statuses = {c["code"]: c["status"] for t in result["terms"] for c in t["courses"]}
    assert statuses == {"CSCI 1044": "planned", "MATH 1103": "done", "CYBR 1003": "in_progress",
                        "PEAC 1001": "planned"}
    assert warnings(result, "2025FA", "CSCI 1044", "past_ungraded")


def test_course_counts_once_per_program_and_pools_respect_limits(data):
    terms = [("TR", [{"code": c, "grade": "A"} for c in ("CYBR 1003", "CYBR 2013", "CYBR 4903", "MATH 1103")]),
             ("2027SP", ["CSCI 1044", "CSCI 1053", "CYBR 3013"])]
    result = check_plan(plan(*terms), data, FALL_2026)
    core = node(result, "major-cyber", 0)
    assert core["status"] == "done"
    assert [i["chosen"] for i in core["items"][2]["items"]] == [False, True]
    pool = node(result, "major-cyber", 1)
    codes = [c["code"] for c in pool["courses"]]
    assert "CYBR 1003" not in codes  # already used by the fixed requirement
    assert "CYBR 3013" in codes  # satisfies the junior/senior minimum first
    # CSCI 1053 would push CSCI past its 6-hour cap (4 + 3), so the pool stops at 7 of 9 hours.
    assert pool["hours_planned"] == 7 and pool["status"] == "missing"
    assert sum(c["hours"] for c in pool["courses"] if c["code"].startswith("CSCI")) <= 6


def test_pool_minimum_unmet_is_missing_with_message(data):
    result = check_plan(plan(("2027SP", ["CSCI 1044", "CSCI 1053", "CYBR 1003", "CYBR 2013"])), data, FALL_2026)
    pool = node(result, "major-cyber", 1)
    assert pool["status"] == "missing" and "junior/senior" in pool["message"]


def test_a_course_may_count_for_two_programs(data):
    result = check_plan(plan(("2027SP", ["HIST 3013", "MATH 1103"]), programs=("major-cyber", "minor-history")),
                        data, FALL_2026)
    assert node(result, "minor-history", 0)["status"] == "planned"
    assert node(result, "major-cyber", 2)["status"] == "planned"


def test_cross_listed_course_satisfies_requirement(data):
    result = check_plan(plan(("2027SP", ["PSCI 3013"]), programs=("minor-history",)), data, FALL_2026)
    assert node(result, "minor-history", 0)["courses"][0]["code"] == "PSCI 3013"


def test_manual_requirements_and_program_status(data):
    result = check_plan(plan(("TR", ["MATH 1103"])), data, FALL_2026)
    core = next(p for p in result["programs"] if p["id"] == "core")
    assert core["status"] == "on_track" and core["has_manual"]
    cyber = next(p for p in result["programs"] if p["id"] == "major-cyber")
    assert cyber["status"] == "incomplete" and cyber["verified"]


def test_graduation_totals_and_minor_message(data):
    result = check_plan(plan(("TR", [{"code": "ENG 1113", "hours": 3}]), ("2027SP", ["CYBR 3013", "MATH 1103"])),
                        data, FALL_2026)
    grad = result["graduation"]
    assert grad["total_hours"]["planned"] == 9
    assert grad["junior_senior_hours"]["planned"] == 3
    assert grad["obu_hours"]["planned"] == 6
    assert any("minor" in m for m in grad["messages"])
    assert warnings(result, "TR", "ENG 1113", "unknown")
    with_minor = check_plan(plan(("2027SP", ["HIST 3013"]), programs=("core", "major-cyber", "minor-history")),
                            data, FALL_2026)
    assert not any("minor" in m for m in with_minor["graduation"]["messages"])


def test_non_catalog_courses_take_hours_from_their_number(data):
    # Off-catalog OBU courses (special topics, 0-hour labs) end in their credit hours; no "set the hours" nag.
    result = check_plan(plan(("2027SP", ["ASL 1023", "PHYS 2030"]), ("TR", ["ENG 101"])), data, FALL_2026)
    assert result["graduation"]["total_hours"]["planned"] == 3
    hours_msgs = lambda code, term="2027SP": [w for w in warnings(result, term, code, "unknown") if "hours" in w["message"].lower()
                                              and "counts for hours" not in w["message"]]
    assert not hours_msgs("ASL 1023") and not hours_msgs("PHYS 2030")
    assert hours_msgs("ENG 101", "TR")  # transfer-style number: hours really are unknown
    explicit = check_plan(plan(("2027SP", [{"code": "ASL 1023", "hours": 4}])), data, FALL_2026)
    assert explicit["graduation"]["total_hours"]["planned"] == 4


def test_major_c_or_better_excludes_d_grades(data):
    result = check_plan(plan(("TR", [{"code": "CYBR 1003", "grade": "D"}, {"code": "MATH 1103", "grade": "A"},
                                     {"code": "CYBR 2013", "grade": "C"}])), data, FALL_2026)
    assert result["graduation"]["major_c_or_better_hours"]["done"] == 3


@pytest.mark.parametrize("bad", [
    {"catalog_year": "1999-00"},
    {"terms": [{"id": "2027XX", "courses": []}]},
    {"terms": [{"id": "2027SP", "courses": [{"code": "not a code"}]}]},
    {"terms": [{"id": "2027SP", "courses": [{"code": "CYBR 1003", "hours": 40}]}]},
    {"terms": [{"id": "2027SP", "courses": []}, {"id": "2027SP", "courses": []}]},
    {"programs": [f"p{i}" for i in range(9)]},
])
def test_malformed_plans_are_rejected(data, bad):
    with pytest.raises(PlanError):
        check_plan({"catalog_year": "2026-27", **bad}, data, FALL_2026)
