from html import escape
from urllib.parse import parse_qs

import httpx
import pytest

from obu_course_planner import schedule as mod

HEADERS = ["", "Seq", "Dept", "Course", "Title", "Max", "Enrolled", "Pet", "T-Slot", "Term", "Days", "Time", "Room", "Instructor"]
CHOICES = {
    "SEM_filt": ["126", "227"], "MLLOC_filt": ["RES", ""],
    "CLDPCR_filt": ["", "CYBR", "CHAP"], "CLSCHL_filt": ["", "I"],
    "CLTIME_filt": ["", "G"], "MLMOD_filt": ["", "A"],
    "CourseLevel_filt": ["", "3"], "CourseHours_filt": ["", "30"],
}
LABELS: dict[str, str] = {}


def course(seq, dept="CYBR", enrolled="2", capacity="16", number="3023.0", slot="G", days="MWF", time="11:00 - 11:50"):
    values = ["", str(seq), dept, number, "FORENSICS", capacity, enrolled, "", slot, "Spring Semester", days, time, "JSC343", "Anderson, Scott"]
    return "<tr>" + "".join(f"<td>{v}</td>" for v in values) + "</tr>"


def school(name):
    return f"<tr><td></td><th colspan='4'><h2>{name}</h2></th><th colspan='5'><h2>Dean: X</h2></th></tr>"


def page(fields=None, rows="", count=0, next_number=2, heading="Patterson School of Natural Sciences"):
    fields = fields or {}
    selects = "".join(
        f'<select name="{name}">' + "".join(
            f'<option value="{value}" {"selected" if value == fields.get(name, choices[0]) else ""}>{LABELS.get(value, value)}</option>'
            for value in choices
        ) + "</select>" for name, choices in CHOICES.items()
    )
    return (
        '<form action="CLASSSCHED.php"><input name="task" value="filter">'
        f'<input name="oldSEM" value="{fields.get("SEM_filt", "126")}">{selects}</form>'
        '<table class="mainlist"><tr>' + "".join(f"<th>{escape(h)}</th>" for h in HEADERS)
        + "</tr>" + (school(heading) if heading and rows else "") + rows + "</table>"
        + f'<script>if ({count} == 100) {{ xl_GetObj("nextlinktop").href="CLASSSCHED.php?page={next_number}&fromfile=PUBLICFRAME"; }}</script>'
    )


class FakeSession:
    """In-memory upstream for parser/capture tests; native HTTP pacing has Rust tests."""

    def __init__(self, handler):
        self._http = httpx.Client(transport=httpx.MockTransport(handler))
        self.requests = 0

    def request(self, method, url, **kwargs):
        self.requests += 1
        response = self._http.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"OBU schedule request failed (HTTP {response.status_code})")
        return response.text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._http.close()


def client_for(handler):
    return mod.ScheduleClient(FakeSession(handler))


def test_school_heading_and_derived_hours_carry_across_pages():
    second = page(rows=course(2, number="1000.5"), count=1, heading=None)
    source = client_for(lambda request: httpx.Response(200, text=second))
    soup, selected, _ = mod.parse_form(page(rows=course(1), count=100))
    rows, pages = source.collect(soup, selected)
    assert pages == 2 and source.requests == 1
    assert [(r["school"], r["credit_hours"]) for r in rows] == [
        ("Patterson School of Natural Sciences", 3.0), ("Patterson School of Natural Sciences", 0.5)]


def test_full_and_zero_capacity_sections_are_kept_with_computed_seats():
    soup, selected, _ = mod.parse_form(page(rows=course(1, enrolled="2") + course(2, enrolled="19") + course(3, enrolled="0", capacity="0"), count=3))
    rows, _ = client_for(lambda request: httpx.Response(500)).collect(soup, selected)
    assert [r["seats_available"] for r in rows] == [14, 0, 0]


def test_select_clears_stale_filters_and_requires_retained_semester():
    seen = {}
    def handler(request):
        seen.update({k: v[0] for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()})
        return httpx.Response(200, text=page(seen, course(1), count=1))
    _, form, _ = mod.parse_form(page({"CLDPCR_filt": "CYBR"}))
    _, selected, _ = client_for(handler).select(form, "227", "RES")
    assert selected["SEM_filt"] == "227" and seen["CLDPCR_filt"] == ""
    ignored = client_for(lambda request: httpx.Response(200, text=page(rows=course(1), count=1)))
    with pytest.raises(RuntimeError, match="did not retain"):
        ignored.select(form, "227", "RES")


def test_notes_attach_and_unknown_petitions_stay_unknown():
    rows = []
    soup, _, _ = mod.parse_form(page(rows=course(993), count=1))
    _, current = mod.parse_rows(soup, rows)
    note = '<tr><td></td><td colspan="3"></td><td colspan="10"> - Permission&nbsp;required</td></tr>'
    mod.parse_rows(mod.parse_form(page(rows=note, heading=None))[0], rows, current)
    assert rows[0]["notes"] == ["Permission required"] and rows[0]["petitions"] is None


def test_row_without_school_heading_is_error_not_guess():
    with pytest.raises(RuntimeError, match="school heading"):
        mod.parse_rows(mod.parse_form(page(rows=course(1), heading=None))[0], [])


@pytest.mark.parametrize("number,hours", [("3023.0", 3.0), ("1000.5", 0.5), ("4990.0", 0.0), (".0", None)])
def test_credit_hours_from_course_number(number, hours):
    assert mod.credit_hours(number) == hours


def test_repeated_or_mixed_pages_are_errors_not_partial_success():
    soup, selected, _ = mod.parse_form(page(rows=course(1), count=100))
    with pytest.raises(RuntimeError, match="repeated a course"):
        client_for(lambda request: httpx.Response(200, text=page(rows=course(1), count=100))).collect(soup, selected)
    with pytest.raises(RuntimeError, match="lost the selected filters"):
        client_for(lambda request: httpx.Response(200, text=page({"SEM_filt": "227"}, course(2), count=1))).collect(soup, selected)


def test_blocked_or_unknown_layout_fails_instead_of_claiming_no_classes():
    with pytest.raises(RuntimeError, match="HTTP 403"):
        client_for(lambda request: httpx.Response(403)).open()
    with pytest.raises(RuntimeError, match="public schedule form"):
        mod.parse_form("<h1>Maintenance</h1>")
