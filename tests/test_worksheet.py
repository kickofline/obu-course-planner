import copy

import pytest

from obu_course_planner.worksheet import parse_days, parse_time, render


def section(seq, dept="CYBR", number="3023.0", days="MWF", time="11:00 - 11:50", hours=3.0, **extra):
    return {"sequence": seq, "department": dept, "course": number, "title": "FORENSICS", "time_slot": "G",
            "days": days, "time": time, "room": "JSC343", "credit_hours": hours, "notes": [], **extra}


def sheet(*courses, **kwargs):
    return render({"courses": list(courses), "semester": "227", "semester_label": "Spring 2027",
                   "snapshot_at": "2026-10-27T23:31:14+00:00", "check_url": "https://info.obu.edu/x"}, **kwargs)


@pytest.mark.parametrize("raw,days", [("MWF", ["M", "W", "F"]), ("TTH", ["T", "TH"]), ("TWTH", ["T", "W", "TH"]),
                                      ("W&F", ["W", "F"]), ("MONDAY", ["M"]), ("TBA", None), ("ONLINE", None), ("", None)])
def test_obu_day_strings(raw, days):
    assert parse_days(raw) == days


def test_afternoon_and_evening_times_are_pm():
    assert parse_time("2:00 - 3:15") == (14 * 60, 15 * 60 + 15)
    assert parse_time("7:00 - 8:15") == (19 * 60, 20 * 60 + 15)
    assert parse_time("11:00 - 12:15") == (660, 735)
    assert parse_time("0:00 - 0:00") is None


def test_overlaps_and_chapel_conflicts_are_reported():
    result = sheet(section("1"), section("2", dept="MATH", days="MW", time="11:30 - 12:20"),
                   section("3", dept="HIST", days="T", time="10:00 - 11:15"))
    assert any("Monday" in c and "CYBR 3023" in c and "MATH 3023" in c for c in result["conflicts"])
    assert any("Tuesday" in c and "Chapel" in c for c in result["conflicts"])
    assert "Time conflicts" in result["html"]


def test_non_meeting_sections_listed_not_placed_and_hours_totaled():
    result = sheet(section("1"), section("2", days="ONLINE", time="0:00 - 0:00", hours=0.5))
    assert [s["on_calendar"] for s in result["sections"]] == [True, False]
    assert result["total_credit_hours"] == 3.5 and result["credit_hours_complete"]
    assert result["conflicts"] == []
    assert sheet(section("1", hours=None))["credit_hours_complete"] is False


def test_user_text_is_escaped_and_cached_records_untouched():
    original = section("1", title="<script>alert(1)</script>")
    snapshot_copy = copy.deepcopy(original)
    html = sheet(original, student_name="<img src=x onerror=alert(1)>", student_id="123")["html"]
    assert "<script>alert" not in html and "<img src=x" not in html and "&lt;img" in html
    assert original == snapshot_copy
