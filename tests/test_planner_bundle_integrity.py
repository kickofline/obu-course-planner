"""Integrity of the shipped planner data bundles (catalog courses + degree programs).

These files were extracted from OBU's residential catalog PDFs (each records the PDF's SHA-256 in
catalog_sha256) and hand-reviewed; a bad edit (typo in a course code, duplicated id, malformed
requirement node) would silently break degree checks for students.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "src" / "obu_course_planner" / "data"
BUNDLES = sorted((DATA / "planner").glob("*.json"))
YEARS = {"2026-27", "2025-26"}
CODE = re.compile(r"^[A-Z]{2,5} \d{3,4}(?:\.5)?[A-L]?$")  # references may be half-hour (1080.5) or span ranges like 47_3
TERMS = {"fall", "spring", "summer", "winter", "may"}
NODE_TYPES = {"course", "all", "choose", "hours", "manual"}
AREAS = {"major", "additional", "minor", "core"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _expr_codes(expr):
    if expr is None:
        return
    if isinstance(expr, str):
        yield expr
        return
    assert set(expr) <= {"all", "any"} and len(expr) == 1, expr
    for item in next(iter(expr.values())):
        yield from _expr_codes(item)


def _filter_codes(flt: dict):
    assert flt.get("subjects") or flt.get("courses"), flt
    yield from flt.get("courses") or []
    yield from flt.get("exclude") or []


def _check_node(node: dict, where: str, top: bool):
    kind = node.get("type")
    assert kind in NODE_TYPES, f"{where}: bad node type {kind!r}"
    if top:
        assert node.get("area") in AREAS, f"{where}: top-level node needs area"
    if kind == "course":
        yield node["course"]
    elif kind in {"all", "choose"}:
        assert node["items"], f"{where}: empty {kind}"
        if kind == "choose":
            assert 1 <= node.get("count", 1) <= len(node["items"]), f"{where}: choose count out of range"
        for index, item in enumerate(node["items"]):
            yield from _check_node(item, f"{where}/{index}", False)
    elif kind == "hours":
        assert node["hours"] > 0, where
        yield from _filter_codes(node["from"])
        for limit in node.get("limits") or []:
            assert ("min_hours" in limit) != ("max_hours" in limit), f"{where}: limit needs exactly one bound"
            yield from _filter_codes(limit["from"])
    else:
        assert node.get("text"), f"{where}: manual node without text"


def test_both_catalog_years_ship_under_their_own_name():
    assert {path.stem for path in BUNDLES} == YEARS
    for path in BUNDLES:
        assert _load(path)["catalog_year"] == path.stem


@pytest.mark.parametrize("path", BUNDLES, ids=lambda p: p.stem)
def test_courses_are_well_formed_and_unique(path):
    courses = _load(path)["courses"]
    codes = [course["code"] for course in courses]
    assert len(codes) == len(set(codes))
    known = set(codes)
    for course in courses:
        code = course["code"]
        assert CODE.match(code) and code == f"{course['subject']} {course['number']}", code
        assert 0 <= course["hours_min"] <= course["hours_max"] <= 12, code
        assert course["level"] == int(course["number"][0]) * 1000, code
        assert course["title"] and len(course["title"]) < 100, code
        assert set(course["offered"]["terms"]) <= TERMS, code
        assert course["offered"]["years"] in {None, "odd", "even"}, code
        assert course["standing"] in {None, "sophomore", "junior", "senior"}, code
        for field in ("prereq", "coreq"):
            for ref in _expr_codes(course.get(field)):
                assert CODE.match(ref), f"{code} {field} {ref!r}"
                assert ref != code, f"{code} requires itself"
        for ref in course.get("cross_listed") or []:
            assert CODE.match(ref), f"{code} cross_listed {ref!r}"
    assert len(known) > 700


@pytest.mark.parametrize("path", BUNDLES, ids=lambda p: p.stem)
def test_programs_are_well_formed(path):
    bundle = _load(path)
    known = {course["code"] for course in bundle["courses"]}
    ids = [program["id"] for program in bundle["programs"]]
    assert len(ids) == len(set(ids))
    assert sum(program["kind"] == "core" for program in bundle["programs"]) == 1
    for program in bundle["programs"]:
        pid = program["id"]
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", pid), pid
        assert program["kind"] in {"core", "major", "minor"}, pid
        assert program["pages"] and all(1 <= page <= 214 for page in program["pages"]), pid
        assert program["requirements"], pid
        assert program["verified"] == bool(program["verified_source"]), pid
        for index, node in enumerate(program["requirements"]):
            for ref in _check_node(node, f"{pid}/{index}", True):
                assert CODE.match(ref), f"{pid}: bad code {ref!r}"
                # Requirements may name courses missing from this catalog's listings only when
                # the extraction recorded why (e.g. a course described in another catalog year).
                if ref not in known:
                    assert any(ref in note for note in program.get("extraction_notes", [])), (
                        f"{pid}: {ref} is not a catalog course and no extraction note explains it")


def test_verified_programs_name_their_degree_sheet():
    verified = [program for path in BUNDLES for program in _load(path)["programs"] if program["verified"]]
    assert verified, "the degree-sheet-reviewed programs must stay marked verified"
    assert all("degree sheet" in program["verified_source"] for program in verified)
    marked = {(path.stem, program["id"]) for path in BUNDLES for program in _load(path)["programs"] if program["verified"]}
    assert ("2025-26", "major-cybersecurity-ba") in marked
    assert ("2025-26", "major-computer-science-bs") in marked
