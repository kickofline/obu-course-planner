"""Deterministic degree-plan checks over the extracted catalog bundles.

A *plan* is the browser's state: catalog year, selected programs, and course placements per term.
Checking never guesses: prerequisites, offerings, standing, and requirement progress come only from
the structured catalog data in ``data/planner/<year>.json``. Text the data cannot express is
reported as advisory (``manual``) rather than silently passed or failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from threading import Lock

PLANNER_DIR = Path(__file__).parent / "data" / "planner"
DEFAULT_YEAR = "2026-27"
SEASONS = {"WI": "winter", "SP": "spring", "MA": "may", "SU": "summer", "FA": "fall"}
SEASON_ORDER = {"WI": 0, "SP": 1, "MA": 2, "SU": 3, "FA": 4}
SEASON_LABEL = {"WI": "Winter", "SP": "Spring", "MA": "May Term", "SU": "Summer", "FA": "Fall"}
TERM_ID = re.compile(r"^(\d{4})(WI|SP|MA|SU|FA)$")
PLAN_CODE = re.compile(r"^[A-Z]{2,5} \d{3,4}(?:\.5|[A-Z])?$")  # 1080.5 = half-hour course (e.g. music ensembles)
PASSING = {"A", "B", "C", "D", "P", "S", "CR"}
C_OR_BETTER = {"A", "B", "C", "P", "S", "CR"}
NOT_EARNED = {"F", "W", "WF", "WP", "I", "NC", "U", "AU", "IP"}
DEFAULT_CLASSIFICATION = {"sophomore": 30, "junior": 60, "senior": 90}
STATUS_RANK = {"done": 0, "in_progress": 1, "planned": 2}
LIMITS = {"programs": 8, "terms": 40, "courses_per_term": 40, "unplaced": 200}


class PlanError(ValueError):
    """The submitted plan is malformed (HTTP 400)."""


class PlannerData:
    """Lazily loaded, cached catalog bundles."""

    def __init__(self, directory: Path = PLANNER_DIR) -> None:
        self._dir = Path(directory)
        self._cache: dict[str, tuple[dict, bytes, str]] = {}
        self._index: dict[str, tuple[bytes, str]] = {}
        self._lock = Lock()

    def years(self) -> list[str]:
        return sorted((path.stem for path in self._dir.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}", path.stem)),
                      reverse=True)

    def _entry(self, year: str) -> tuple[dict, bytes, str]:
        if not re.fullmatch(r"\d{4}-\d{2}", year or ""):
            raise KeyError(year)
        with self._lock:
            if year not in self._cache:
                path = self._dir / f"{year}.json"
                if not path.is_file():
                    raise KeyError(year)
                raw = path.read_bytes()
                bundle = json.loads(raw)
                bundle["_index"] = {c["code"]: c for c in bundle["courses"]}
                bundle["_programs"] = {p["id"]: p for p in bundle["programs"]}
                bundle["_equiv"] = _equivalents(bundle["courses"])
                self._cache[year] = (bundle, raw, hashlib.sha256(raw).hexdigest()[:32])
            return self._cache[year]

    def bundle(self, year: str) -> dict:
        return self._entry(year)[0]

    def raw(self, year: str) -> tuple[bytes, str]:
        _, raw, etag = self._entry(year)
        return raw, etag

    def index(self, year: str) -> tuple[bytes, str]:
        """What the website needs up front: code, title, hours, subject per course (~12 KB gzipped vs
        ~160 KB for the whole bundle). Descriptions load per course; requirement trees stay server-side."""
        bundle, _, etag = self._entry(year)
        with self._lock:
            if year not in self._index:
                courses = [{key: c[key] for key in ("code", "title", "hours_min", "hours_max", "subject", "subject_name")}
                           for c in bundle["courses"]]
                raw = json.dumps({"catalog_year": year, "courses": courses}, separators=(",", ":")).encode()
                self._index[year] = (raw, f"{etag}-i")
            return self._index[year]

    def catalogs(self) -> dict:
        years = []
        for year in self.years():
            bundle = self.bundle(year)
            years.append({
                "catalog_year": year, "default": year == DEFAULT_YEAR,
                "programs": [{key: p.get(key) for key in (
                    "id", "kind", "name", "degree", "emphasis", "department", "verified", "confidence")}
                    for p in bundle["programs"]],
            })
        return {"years": years}


def _equivalents(courses: list[dict]) -> dict[str, frozenset[str]]:
    groups: dict[str, set[str]] = {}
    for course in courses:
        for other in course.get("cross_listed") or []:
            merged = groups.get(course["code"], {course["code"]}) | groups.get(other, {other})
            for code in merged:
                groups[code] = merged
    return {code: frozenset(group) for code, group in groups.items()}


# ---------------------------------------------------------------------------- terms


def term_key(term_id: str) -> tuple[int, int]:
    if term_id == "TR":
        return (-1, 0)
    match = TERM_ID.match(term_id)
    if not match:
        raise PlanError(f"Unknown term id {term_id!r}; use e.g. 2026FA, 2027SP, 2027WI, 2027MA, 2027SU or TR.")
    return int(match[1]), SEASON_ORDER[match[2]]


def term_label(term_id: str) -> str:
    if term_id == "TR":
        return "Transfer / credit by exam"
    match = TERM_ID.match(term_id)
    return f"{SEASON_LABEL[match[2]]} {match[1]}"


def term_for_date(day: date) -> str:
    """Catalog calendar windows: Fall Aug 1-Dec 20, Winter Dec 21-Jan 10, Spring Jan 11-May 9,
    May term May 10-31, Summer Jun 1-Jul 31. Winter is named by its January year."""
    month_day = (day.month, day.day)
    if (8, 1) <= month_day <= (12, 20):
        return f"{day.year}FA"
    if month_day >= (12, 21):
        return f"{day.year + 1}WI"
    if month_day <= (1, 10):
        return f"{day.year}WI"
    if month_day <= (5, 9):
        return f"{day.year}SP"
    if month_day <= (5, 31):
        return f"{day.year}MA"
    return f"{day.year}SU"


def label_to_term_id(label: str) -> str | None:
    match = re.fullmatch(r"\s*(Fall|Spring|Summer|Winter|May(?: Term)?)\s+(\d{4})\s*", label or "", re.I)
    if not match:
        return None
    season = {"fall": "FA", "spring": "SP", "summer": "SU", "winter": "WI"}.get(match[1].lower(), "MA")
    return f"{match[2]}{season}"


# ---------------------------------------------------------------------------- plan parsing


@dataclass
class Placement:
    index: tuple[int, int]
    term: str
    code: str
    hours: float
    grade: str | None
    status: str  # done | in_progress | planned | failed
    course: dict | None
    warnings: list[dict] = field(default_factory=list)
    hours_known: bool = True

    @property
    def level(self) -> int:
        number = self.code.split(" ")[1]
        return int(number[0]) * 1000

    @property
    def subject(self) -> str:
        return self.code.split(" ")[0]

    @property
    def counts(self) -> bool:
        return self.status != "failed"


def _base_grade(grade: str) -> str:
    return re.sub(r"[+-]$", "", grade.strip().upper())


def normalize_code(code: object) -> str:
    text = re.sub(r"\s+", " ", str(code or "")).strip().upper()
    match = re.fullmatch(r"([A-Z]{2,5}) ?(\d{3,4}(?:\.5|[A-Z])?)(?:\.0)?", text)
    return f"{match[1]} {match[2]}" if match else text


def _warn(target: list, severity: str, kind: str, message: str) -> None:
    target.append({"severity": severity, "kind": kind, "message": message})


def parse_plan(plan: object, data: PlannerData) -> tuple[dict, list[dict], list[Placement]]:
    if not isinstance(plan, dict):
        raise PlanError("Plan must be a JSON object.")
    year = plan.get("catalog_year") or DEFAULT_YEAR
    try:
        bundle = data.bundle(year)
    except KeyError as exc:
        raise PlanError(f"Unknown catalog year {year!r}.") from exc
    programs = plan.get("programs") or []
    terms = plan.get("terms") or []
    if not isinstance(programs, list) or not isinstance(terms, list):
        raise PlanError("programs and terms must be lists.")
    if len(programs) > LIMITS["programs"] or len(terms) > LIMITS["terms"]:
        raise PlanError("Too many programs or terms.")
    seen_terms: set[str] = set()
    placements: list[Placement] = []
    clean_terms: list[dict] = []
    for t_index, term in enumerate(terms):
        if not isinstance(term, dict) or not isinstance(term.get("courses", []), list):
            raise PlanError("Each term must be an object with a courses list.")
        term_id = str(term.get("id", ""))
        term_key(term_id)
        if term_id in seen_terms:
            raise PlanError(f"Term {term_id} appears twice.")
        seen_terms.add(term_id)
        courses = term.get("courses", [])
        if len(courses) > LIMITS["courses_per_term"]:
            raise PlanError(f"Too many courses in {term_id}.")
        clean_terms.append({"id": term_id, "t_index": t_index})
        for c_index, raw in enumerate(courses):
            if not isinstance(raw, dict):
                raise PlanError("Each placement must be an object.")
            code = normalize_code(raw.get("code"))
            if not PLAN_CODE.match(code):
                raise PlanError(f"Course code {raw.get('code')!r} is not like 'CYBR 1003'.")
            course = bundle["_index"].get(code)
            hours = raw.get("hours")
            if hours is not None and (not isinstance(hours, (int, float)) or not 0 <= hours <= 12):
                raise PlanError(f"Hours for {code} must be 0-12.")
            grade = raw.get("grade")
            grade = str(grade).strip().upper() if grade not in (None, "") else None
            if grade is not None and len(grade) > 4:
                raise PlanError(f"Grade for {code} is not a grade.")
            if hours is not None:
                resolved, known = float(hours), True
            elif course:
                resolved, known = float(course["hours_min"]), True
            else:
                resolved = _hours_from_number(code)
                known = resolved is not None
            placements.append(Placement((t_index, c_index), term_id, code, resolved or 0.0,
                                        grade, "planned", course, hours_known=known))
    return bundle, clean_terms, placements


def _hours_from_number(code: str) -> float | None:
    """OBU course numbers end in the credit hours (CSCI 2113 = 3, PHYS 2030 = 0-hour lab);
    holds for 931 of 953 catalog courses. Transfer-style 3-digit numbers carry no hours."""
    number = code.split(" ")[1]
    if len(number) == 4 and number.isdigit():
        return float(number[3])
    return None


# ---------------------------------------------------------------------------- checks


def _assign_status(placements: list[Placement], today_term: str) -> None:
    today = term_key(today_term)
    for p in placements:
        if p.course is None:
            _warn(p.warnings, "info", "unknown",
                  f"{p.code} is not in this catalog; it counts for hours and only matches requirements by exact code.")
            if not p.hours_known:
                _warn(p.warnings, "info", "unknown", f"Credit hours for {p.code} are unknown; counting 0.")
        elif p.course["hours_min"] != p.course["hours_max"]:
            _warn(p.warnings, "info", "hours",
                  f"Variable credit ({p.course['hours_min']}-{p.course['hours_max']} hours); counting {p.hours:g}.")
        if p.grade is not None:
            base = _base_grade(p.grade)
            if base in PASSING:
                p.status = "done"
            elif base in NOT_EARNED:
                p.status = "failed"
                _warn(p.warnings, "info", "grade", f"Grade {p.grade} earns no credit; this placement satisfies nothing.")
            else:
                p.status = "failed"
                _warn(p.warnings, "warn", "grade", f"Unrecognized grade {p.grade!r}; not counted.")
        elif p.term == "TR":
            p.status = "done"
        elif term_key(p.term) == today:
            p.status = "in_progress"
        elif term_key(p.term) < today:
            p.status = "planned"
            _warn(p.warnings, "info", "past_ungraded", "This term is over but the course has no grade; add the grade or move it.")
        else:
            p.status = "planned"


def _same(bundle: dict, code: str) -> frozenset[str]:
    return bundle["_equiv"].get(code, frozenset({code}))


def _expr_ok(expr, have: set[str]) -> bool:
    if isinstance(expr, str):
        return expr in have
    if "all" in expr:
        return all(_expr_ok(item, have) for item in expr["all"])
    return any(_expr_ok(item, have) for item in expr["any"])


def _expr_text(expr, top: bool = True) -> str:
    if isinstance(expr, str):
        return expr
    joiner = " and " if "all" in expr else " or "
    text = joiner.join(_expr_text(item, False) for item in next(iter(expr.values())))
    return text if top else f"({text})"


def _offered_problem(course: dict, term_id: str) -> tuple[str, str] | None:
    offered = course.get("offered") or {}
    terms, years, on_demand = offered.get("terms") or [], offered.get("years"), offered.get("on_demand")
    match = TERM_ID.match(term_id)
    season, year = SEASONS[match[2]], int(match[1])
    pattern = course.get("offered_text") or ", ".join(terms)
    if terms and season not in terms:
        if on_demand:
            return "info", f"Catalog: \u201c{pattern}\u201d \u2014 {season} offering would be on demand."
        return "warn", f"Catalog lists this course for {', '.join(terms)} only (\u201c{pattern}\u201d)."
    if terms and years and (year % 2 == 1) != (years == "odd"):
        return "warn", f"Catalog lists this course in {years}-numbered years only (\u201c{pattern}\u201d)."
    if not terms and on_demand:
        return "info", "Catalog: offered on demand. Ask the department when it will run."
    return None


def check_plan(plan: object, data: PlannerData, today: date | None = None,
               offerings: dict[str, set[str]] | None = None, snapshot_label: str | None = None) -> dict:
    bundle, terms, placements = parse_plan(plan, data)
    today_term = term_for_date(today or date.today())
    _assign_status(placements, today_term)
    rules = bundle.get("rules") or {}
    by_term: dict[str, list[Placement]] = {}
    for p in placements:
        by_term.setdefault(p.term, []).append(p)
    ordered = sorted(terms, key=lambda t: term_key(t["id"]))
    seen: dict[frozenset[str], Placement] = {}
    term_results: dict[str, dict] = {}
    for term in ordered:
        term_id = term["id"]
        here = [p for p in by_term.get(term_id, []) if p.counts]
        for p in by_term.get(term_id, []):
            if not p.counts or p.course is None:
                continue
            course = p.course
            if term_id != "TR" and p.status != "done":
                problem = _offered_problem(course, term_id)
                if problem:
                    _warn(p.warnings, problem[0], "offered", problem[1])
                if offerings is not None and term_id in offerings and not (_same(bundle, p.code) & offerings[term_id]):
                    _warn(p.warnings, "warn", "schedule",
                          f"Not in the cached {term_label(term_id)} schedule"
                          + (f" ({snapshot_label})" if snapshot_label else "") + ".")
            group = _same(bundle, p.code)
            if group in seen and not course.get("repeatable"):
                _warn(p.warnings, "warn", "duplicate",
                      f"Also placed in {term_label(seen[group].term)}; repeating it does not add hours.")
            seen.setdefault(group, p)
        hours = sum(p.hours for p in here)
        warnings: list[dict] = []
        match = TERM_ID.match(term_id)
        full_time = rules.get("full_time_hours") or 12
        maximum = rules.get("max_hours_per_semester") or 18
        if match and match[2] in ("FA", "SP") and here:
            if hours < full_time:
                _warn(warnings, "warn", "load", f"{hours:g} hours is below full-time ({full_time}).")
            elif hours > maximum:
                _warn(warnings, "warn", "load", f"{hours:g} hours is above {maximum}; an overload needs approval.")
        status = ("done" if term_id == "TR" or term_key(term_id) < term_key(today_term)
                  else "in_progress" if term_id == today_term else "planned")
        term_results[term_id] = {"id": term_id, "label": term_label(term_id), "hours": hours,
                                 "status": status, "warnings": warnings}

    program_results = [evaluate_program(bundle, pid, placements) for pid in plan.get("programs") or []]
    graduation = _graduation(bundle, placements, program_results, plan)
    out_terms = []
    for term in terms:
        result = term_results[term["id"]]
        result["courses"] = [{"code": p.code, "title": p.course["title"] if p.course else None,
                              "status": p.status, "hours": p.hours, "known": p.course is not None,
                              "warnings": p.warnings}
                             for p in by_term.get(term["id"], [])]
        out_terms.append(result)
    return {"catalog_year": bundle["catalog_year"], "today_term": today_term, "terms": out_terms,
            "programs": program_results, "graduation": graduation, "warnings": []}


# ---------------------------------------------------------------------------- requirements


def _matches(flt: dict, p: Placement, bundle: dict) -> bool:
    group = _same(bundle, p.code)
    if group & set(flt.get("exclude") or []):
        return False
    if group & set(flt.get("courses") or []):
        return True
    if p.subject not in (flt.get("subjects") or []):
        return False
    low, high = flt.get("min_level"), flt.get("max_level")
    return (low is None or p.level >= low) and (high is None or p.level <= high)


def _rank(p: Placement) -> tuple:
    return (STATUS_RANK[p.status], term_key(p.term), p.index)


def _agg(statuses: list[str]) -> str:
    for status in ("missing", "planned", "manual"):
        if status in statuses:
            return status
    return "done"


class _Allocator:
    def __init__(self, bundle: dict, placements: list[Placement]) -> None:
        self.bundle = bundle
        self.pool = sorted((p for p in placements if p.counts and p.term), key=_rank)
        self.used: set[tuple[int, int]] = set()

    def find(self, code: str) -> Placement | None:
        group = _same(self.bundle, code)
        return next((p for p in self.pool if p.index not in self.used and p.code in group), None)

    def eval(self, node: dict) -> dict:
        kind = node["type"]
        out = {k: v for k, v in node.items() if k != "items"}
        if kind == "course":
            p = self.find(node["course"])
            if p:
                self.used.add(p.index)
                out.update(status="done" if p.status == "done" else "planned",
                           courses=[{"code": p.code, "term": p.term, "status": p.status, "hours": p.hours}])
            else:
                course = self.bundle["_index"].get(node["course"])
                out.update(status="missing", courses=[], title=course["title"] if course else None)
            return out
        if kind == "manual":
            out.update(status="manual", courses=[])
            return out
        if kind == "hours":
            return self._hours(node, out)
        if kind == "all":
            items = [self.eval(item) for item in node["items"]]
            out.update(items=items, status=_agg([i["status"] for i in items]))
            return out
        # choose: try each option on a scratch copy, keep the best `count`.
        count = node.get("count", 1)
        trials = []
        for position, item in enumerate(node["items"]):
            saved = set(self.used)
            result = self.eval(item)
            self.used = saved
            score = {"done": 0, "planned": 1, "manual": 2, "missing": 3}[result["status"]]
            trials.append((score, -_progress(result), position))
        chosen = {position for _, _, position in sorted(trials)[:count]}
        items = []
        for position, item in enumerate(node["items"]):
            if position in chosen:
                result = self.eval(item)
            else:
                result = _unallocated(item, self.bundle)
            result["chosen"] = position in chosen
            items.append(result)
        status = _agg([i["status"] for i in items if i["chosen"]])
        out.update(items=items, status=status)
        return out

    def _hours(self, node: dict, out: dict) -> dict:
        need = node["hours"]
        limits = node.get("limits") or []
        candidates = [p for p in self.pool if p.index not in self.used and _matches(node["from"], p, self.bundle)]
        taken: list[Placement] = []

        def total(selection, flt=None, done_only=False):
            return sum(p.hours for p in selection if (flt is None or _matches(flt, p, self.bundle))
                       and (not done_only or p.status == "done"))

        def fits(p):
            return all(total(taken + [p], lim["from"]) <= lim["max_hours"] for lim in limits if "max_hours" in lim)

        for lim in limits:
            if "min_hours" in lim:
                for p in candidates:
                    if total(taken, lim["from"]) >= lim["min_hours"]:
                        break
                    if p not in taken and _matches(lim["from"], p, self.bundle) and fits(p):
                        taken.append(p)
        for p in candidates:
            if total(taken) >= need:
                break
            if p not in taken and fits(p):
                taken.append(p)
        self.used |= {p.index for p in taken}

        def met(done_only):
            return total(taken, done_only=done_only) >= need and all(
                total(taken, lim["from"], done_only) >= lim["min_hours"] for lim in limits if "min_hours" in lim)

        status = "done" if met(True) else "planned" if met(False) else "missing"
        messages = [f"At least {lim['min_hours']} of these hours must match: {_describe(lim['from'])}."
                    for lim in limits if "min_hours" in lim and total(taken, lim["from"]) < lim["min_hours"]]
        out.update(status=status, hours_required=need, hours_done=total(taken, done_only=True),
                   hours_planned=total(taken), courses=[{"code": p.code, "term": p.term, "status": p.status,
                                                         "hours": p.hours} for p in taken],
                   description=_describe(node["from"]), message=" ".join(messages) or None)
        return out


def _progress(result: dict) -> float:
    if "hours_planned" in result:
        return result["hours_planned"]
    return len(result.get("courses", [])) + sum(_progress(i) for i in result.get("items", []))


def _unallocated(node: dict, bundle: dict) -> dict:
    out = {k: v for k, v in node.items() if k != "items"}
    out["status"] = "manual" if node["type"] == "manual" else "missing"
    out["courses"] = []
    if node["type"] == "course":
        course = bundle["_index"].get(node["course"])
        out["title"] = course["title"] if course else None
    if node["type"] == "hours":
        out.update(hours_required=node["hours"], hours_done=0, hours_planned=0, description=_describe(node["from"]))
    if "items" in node:
        out["items"] = [_unallocated(item, bundle) for item in node["items"]]
    return out


def _describe(flt: dict) -> str:
    parts = []
    if flt.get("subjects"):
        level = ""
        if flt.get("min_level"):
            level = f" {flt['min_level']}-level or above" if flt["min_level"] != 3000 else " junior/senior-level"
        if flt.get("max_level"):
            level += f" up to {flt['max_level']}-level"
        parts.append("/".join(flt["subjects"]) + level)
    if flt.get("courses"):
        parts.append(", ".join(flt["courses"]))
    text = " or ".join(parts)
    if flt.get("exclude"):
        text += f" (excluding {', '.join(flt['exclude'])})"
    return text


def evaluate_program(bundle: dict, program_id: str, placements: list[Placement]) -> dict:
    program = bundle["_programs"].get(program_id)
    if program is None:
        return {"id": program_id, "status": "unknown", "message": f"No program {program_id!r} in {bundle['catalog_year']}."}
    allocator = _Allocator(bundle, placements)
    # Fixed course requirements claim their courses before alternatives and hour pools do.
    order = sorted(range(len(program["requirements"])),
                   key=lambda i: {"course": 0, "all": 1, "choose": 2, "manual": 3, "hours": 4}[program["requirements"][i]["type"]])
    results: list[dict | None] = [None] * len(order)
    for i in order:
        results[i] = allocator.eval(program["requirements"][i])
    status = _agg([r["status"] for r in results])
    used = [p for p in allocator.pool if p.index in allocator.used]
    area_hours: dict[str, dict] = {}
    for result in results:
        bucket = area_hours.setdefault(result.get("area", "major"), {"done": 0.0, "planned": 0.0})
        for p in _collect(result):
            bucket["planned"] += p["hours"]
            if p["status"] == "done":
                bucket["done"] += p["hours"]
    return {
        "id": program_id, "name": program["name"], "kind": program["kind"], "degree": program.get("degree"),
        "emphasis": program.get("emphasis"), "verified": program.get("verified", False),
        "verified_source": program.get("verified_source"), "confidence": program.get("confidence"),
        "pages": program.get("pages", []), "notes": program.get("notes", []),
        "requires_minor": program.get("requires_minor"),
        "status": {"done": "complete", "planned": "on_track", "manual": "on_track"}.get(status, "incomplete"),
        "has_manual": "manual" in _statuses(results),
        "hours": {"done": sum(p.hours for p in used if p.status == "done"),
                  "planned": sum(p.hours for p in used), "required": program.get("stated_hours")},
        "area_hours": area_hours,
        "requirements": results,
    }


def _collect(result: dict) -> list[dict]:
    found = list(result.get("courses", [])) if result.get("chosen", True) else []
    for item in result.get("items", []):
        if item.get("chosen", True):
            found += _collect(item)
    return found


def _statuses(results: list[dict]) -> set[str]:
    out = set()
    for r in results:
        out.add(r["status"])
        out |= _statuses(r.get("items", []))
    return out


def _graduation(bundle: dict, placements: list[Placement], programs: list[dict], plan: dict) -> dict:
    rules = bundle.get("rules") or {}
    counted: dict[frozenset[str], Placement] = {}
    for p in sorted((p for p in placements if p.counts), key=_rank):
        group = _same(bundle, p.code)
        if p.course is not None and p.course.get("repeatable"):
            group = frozenset({f"{p.code}#{p.index}"})
        counted.setdefault(group, p)
    unique = list(counted.values())

    def tally(selection, required):
        return {"done": sum(p.hours for p in selection if p.status == "done"),
                "planned": sum(p.hours for p in selection), "required": required}

    major_codes = set()
    for result in programs:
        if result.get("kind") == "major":
            for top in result.get("requirements", []):
                if top.get("area", "major") == "major":
                    major_codes |= {c["code"] for c in _collect(top)}
    major_c = [p for p in unique if p.code in major_codes and (p.grade is None or _base_grade(p.grade) in C_OR_BETTER)]
    selected = {pid: bundle["_programs"].get(pid) for pid in plan.get("programs") or []}
    kinds = [p["kind"] for p in selected.values() if p]
    messages = []
    minor_required = bool(rules.get("requires_minor_default", True))
    if any(p and p["kind"] == "major" and p.get("requires_minor") is False for p in selected.values()):
        minor_required = False
    if minor_required and "minor" not in kinds and kinds.count("major") < 2:
        messages.append("A minor (or a second major) is generally required; see the catalog's degree requirements"
                        + (f": {rules['minor_exceptions_text']}" if rules.get("minor_exceptions_text") else "."))
    if "core" not in selected:
        messages.append("Add the Core Curriculum to check general-education requirements.")
    chapel = rules.get("chapel_credits")
    if chapel:
        messages.append(f"Chapel: {chapel} chapel credits are required (not tracked here).")
    if rules.get("min_gpa"):
        messages.append(f"Minimum GPA {rules['min_gpa']:.3f} overall, at OBU, in the major and in the minor (not computed here).")
    residence_last = rules.get("residence_last") or {}
    if residence_last.get("hours"):
        messages.append(f"{residence_last['hours']} of the last {residence_last.get('of_last')} hours must be taken at OBU.")
    return {
        "total_hours": tally(unique, rules.get("total_hours") or 120),
        "junior_senior_hours": tally([p for p in unique if p.level >= 3000], rules.get("junior_senior_hours") or 39),
        "obu_hours": tally([p for p in unique if p.term != "TR"], rules.get("residence_hours") or 60),
        "major_c_or_better_hours": tally(major_c, rules.get("major_c_or_better_hours") or 24),
        "minor_required": minor_required,
        "messages": messages,
    }
