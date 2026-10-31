"""Read-only client for OBU's public schedule form, used only by the snapshot refresher."""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from .cfsolve_transport import CfsolveSession

SOURCE_URL = "https://info.obu.edu/info/p3/CLASSSCHED.php"
FRAME_URL = SOURCE_URL + "?fromfile=PublicFrame&pubiframe=Y"
# Every selector on the source form; all must be submitted (blank) to get an unfiltered listing.
SELECTS = {
    "semester": "SEM_filt", "school": "CLSCHL_filt",
    "department": "CLDPCR_filt", "time_slot": "CLTIME_filt",
    "campus": "MLLOC_filt", "term": "MLMOD_filt",
    "course_level": "CourseLevel_filt", "course_hours": "CourseHours_filt",
}
EXPECTED_COLUMNS = ["", "Seq", "Dept", "Course", "Title", "Max", "Enrolled", "Pet", "T-Slot", "Term", "Days", "Time", "Room", "Instructor"]


def text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def parse_form(html: str) -> tuple[BeautifulSoup, dict, dict]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", action="CLASSSCHED.php")
    if form is None or soup.select_one("table.mainlist") is None:
        raise RuntimeError("OBU did not return the public schedule form; its format or availability may have changed.")
    payload = {n["name"]: n.get("value", "") for n in form.select("input[name]")}
    options = {}
    for key, name in SELECTS.items():
        select = form.find("select", attrs={"name": name})
        if select is None:
            raise RuntimeError(f"OBU schedule is missing the {key} selector.")
        choices = select.find_all("option")
        selected = select.select_one("option[selected]") or (choices[0] if choices else None)
        if selected is None:
            raise RuntimeError(f"OBU schedule has no {key} choices.")
        payload[name] = selected.get("value", "")
        options[key] = [
            {"value": o.get("value", "").strip(), "label": text(o), "selected": o is selected}
            for o in choices
        ]
    return soup, payload, options


def choose(options: dict, key: str, value: str) -> str:
    value = value.strip()
    for option in options[key]:
        if value.casefold() in (option["value"].casefold(), option["label"].casefold()):
            return option["value"]
    raise ValueError(f"Unknown {key} {value!r}. Use get_schedule_options for the values available in this semester/campus.")


def number(value: str, field: str) -> int | None:
    if not value:
        return None
    if not re.fullmatch(r"-?\d[\d,]*", value):
        raise RuntimeError(f"Unrecognized OBU {field} value: {value!r}; refusing to infer availability.")
    return int(value.replace(",", ""))


def credit_hours(course: str) -> float | None:
    """OBU course numbers end in their credit hours; a .5 suffix adds a half hour (1000.5 -> 0.5)."""
    match = re.fullmatch(r"\d{3}(\d)\.(\d)", course)
    return int(match[1]) + int(match[2]) / 10 if match else None


def parse_rows(soup: BeautifulSoup, courses: list[dict], school: str | None = None) -> tuple[int, str | None]:
    """Append records and attach continuation notes, including across page boundaries.

    Schools appear as <h2> group headings; the current one carries over to the next page.
    """
    table = soup.select_one("table.mainlist")
    if [text(h) for h in table.select("tr:first-child > th")] != EXPECTED_COLUMNS:
        raise RuntimeError("OBU schedule columns changed; refusing to return mislabelled courses.")
    count = 0
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        values = [text(c) for c in cells]
        heading = row.find("h2")
        if heading is not None:
            school = text(heading)
        elif len(values) == 14:
            if not values[1].isdigit() or not values[2]:
                raise RuntimeError("Unrecognized OBU schedule course row.")
            if school is None:
                raise RuntimeError("OBU schedule row has no school heading; refusing to guess its school.")
            capacity, enrolled = number(values[5], "capacity"), number(values[6], "enrollment")
            available = None if capacity is None or enrolled is None else max(0, capacity - enrolled)
            markers = [n.get("title") or n.get("alt") for n in cells[0].select("[title], [alt]")]
            courses.append({
                "sequence": values[1], "department": values[2], "course": values[3],
                "course_code": f"{values[2]} {values[3]}", "title": values[4],
                "credit_hours": credit_hours(values[3]), "school": school,
                "capacity": capacity, "enrolled": enrolled,
                "petitions": number(values[7], "petitions"), "seats_available": available,
                "time_slot": values[8], "term": values[9], "days": values[10],
                "time": values[11], "room": values[12], "instructor": values[13],
                "status": values[0], "status_markers": [m for m in markers if m], "notes": [],
            })
            count += 1
        elif len(cells) == 3 and cells[-1].get("colspan") == "10":
            if courses:
                courses[-1]["notes"].append(values[-1].removeprefix("- "))
        elif row.find(["h3", "th"]) is None and any(values):
            raise RuntimeError("Unrecognized OBU schedule row; refusing to omit possible course information.")
    return count, school


def next_page(soup: BeautifulSoup) -> int | None:
    # OBU assigns links in JavaScript rather than rendering anchor hrefs.
    script = "\n".join(s.get_text() for s in soup.find_all("script"))
    match = re.search(
        r'if\s*\(\s*(\d+)\s*==\s*100\s*\)\s*\{[^}]*?nextlinktop[^\n]*?href\s*=\s*"CLASSSCHED\.php\?page=(\d+)&',
        script, re.S,
    )
    if match is None:
        raise RuntimeError("OBU pagination format changed; cannot establish whether results are complete.")
    return int(match[2]) if int(match[1]) == 100 else None


class ScheduleClient:
    """Read listing pages in one persistent, network-paced native session."""

    def __init__(self, client: CfsolveSession) -> None:
        self._client = client
        self.requests = 0

    def request(self, method: str, url: str, **kwargs: Any) -> str:
        self.requests += 1
        return self._client.request(method, url, **kwargs)

    def open(self) -> tuple[BeautifulSoup, dict, dict]:
        return parse_form(self.request("GET", FRAME_URL))

    def select(self, form: dict, semester: str, campus: str) -> tuple[BeautifulSoup, dict, dict]:
        """Unfiltered listing for one semester/program, built on the latest returned form."""
        payload = dict(form)
        for name in SELECTS.values():
            payload[name] = ""
        payload.update(task="filter", SEM_filt=semester, MLLOC_filt=campus,
                       CLROOM_filt="", CLINST_filt="", CLSEQ_filt="")
        soup, selected, options = parse_form(self.request("POST", SOURCE_URL, data=payload))
        if any(selected[name].strip() != payload[name].strip() for name in SELECTS.values()):
            raise RuntimeError(f"OBU did not retain semester {semester}/program {campus or 'all'}; snapshot not replaced.")
        return soup, selected, options

    def collect(self, soup: BeautifulSoup, selected: dict) -> tuple[list[dict], int]:
        """Every page of the current selection, failing rather than returning partial or mixed data."""
        courses: list[dict] = []
        identities = set()
        visited = {1}
        school = None
        while True:
            start = len(courses)
            _, school = parse_rows(soup, courses, school)
            for course in courses[start:]:
                identity = (course["sequence"], course["department"], course["course"])
                if identity in identities:
                    raise RuntimeError("OBU repeated a course across pages; results may have changed during retrieval.")
                identities.add(identity)
            page = next_page(soup)
            if page is None:
                return courses, len(visited)
            if page in visited or len(visited) >= 50:
                raise RuntimeError("OBU pagination did not terminate within 50 pages; no partial results returned.")
            visited.add(page)
            soup, page_selected, _ = parse_form(self.request("GET", SOURCE_URL, params={"page": page, "fromfile": "PUBLICFRAME"}))
            if any(page_selected[name].strip() != selected[name].strip() for name in SELECTS.values()):
                raise RuntimeError("OBU lost the selected filters during pagination; no mixed results returned.")
