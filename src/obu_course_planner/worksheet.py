"""Render OBU's Pre-Registration Schedule worksheet as a self-contained, color-coded HTML page."""
from __future__ import annotations

from datetime import datetime
from html import escape
import re

from . import config

# (fill, border/text) pairs that stay legible when printed in color or grayscale.
PALETTE = [
    ("#cfe2ff", "#0a4fb5"), ("#d1f2d9", "#17733f"), ("#ffe1b8", "#b35c00"),
    ("#f8d4d8", "#a52834"), ("#e4dbf5", "#5b3aa8"), ("#cff0f5", "#0b6676"),
    ("#fff1b3", "#8a6800"), ("#f6d3e5", "#962051"), ("#dde3ff", "#2f45b0"),
    ("#d8f5d0", "#2c7a1f"), ("#ffd8c2", "#c2410c"), ("#e6e6e6", "#444444"),
]
DAY_NAMES = {"MONDAY": "M", "TUESDAY": "T", "WEDNESDAY": "W", "THURSDAY": "TH", "FRIDAY": "F", "SATURDAY": "S"}
DAY_ORDER = ["M", "T", "W", "TH", "F", "S"]
DAY_LABELS = {"M": "MONDAY", "T": "TUESDAY", "W": "WEDNESDAY", "TH": "THURSDAY", "F": "FRIDAY", "S": "SATURDAY"}
HOUR_PX = 42
CHAPEL = {"days": ["T"], "start": 600, "end": 650}


def parse_days(raw: str) -> list[str] | None:
    """OBU day strings (MWF, TTH, TWTH, W&F, MONDAY) -> ordered day codes; None if not a meeting pattern."""
    value = raw.upper().replace("&", "").replace(" ", "")
    value = DAY_NAMES.get(value, value)
    if not value or not re.fullmatch(r"(?:TH|M|T|W|F|S)+", value):
        return None
    return list(dict.fromkeys(re.findall(r"TH|M|T|W|F|S", value)))


def to_minutes(clock: str) -> int:
    hour, minute = map(int, clock.split(":"))
    if 1 <= hour <= 7:
        hour += 12
    return hour * 60 + minute


def parse_time(raw: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*", raw)
    if not match or match[1] == "0:00":
        return None
    start, end = to_minutes(match[1]), to_minutes(match[2])
    if end <= start:
        end += 12 * 60  # e.g. evening 7:00 - 8:15
    return (start, end) if end - start <= 6 * 60 else None


def course_label(course: dict) -> str:
    number = re.sub(r"\.0$", "", course["course"])
    return f'{course["department"]} {number}'


def short_clock(minutes: int) -> str:
    """Same as the website: 9:00a, 1:50p."""
    hour, minute = divmod(minutes, 60)
    return f"{(hour - 1) % 12 + 1}:{minute:02d}{'a' if hour < 12 else 'p'}"


def day_text(days: list[str]) -> str:
    return "".join("Th" if d == "TH" else d for d in days)


def layout(events: list[dict]) -> None:
    """Side-by-side columns for overlapping blocks in one day."""
    events.sort(key=lambda e: (e["start"], e["end"]))
    cluster: list[dict] = []
    cluster_end = -1
    def close() -> None:
        width = max((e["col"] for e in cluster), default=-1) + 1
        for event in cluster:
            event["cols"] = width
    for event in events:
        if cluster and event["start"] >= cluster_end:
            close()
            cluster, cluster_end = [], -1
        used = {e["col"] for e in cluster if e["end"] > event["start"]}
        event["col"] = next(i for i in range(len(cluster) + 1) if i not in used)
        cluster.append(event)
        cluster_end = max(cluster_end, event["end"])
    close()


def render(sections: dict, student_name: str = "", student_id: str = "") -> dict:
    # Copies: snapshot records are shared, cached objects.
    courses = [dict(c) for c in sections["courses"]]
    placed, unscheduled = [], []
    by_day: dict[str, list[dict]] = {day: [] for day in DAY_ORDER}
    for index, course in enumerate(courses):
        fill, ink = PALETTE[index % len(PALETTE)]
        course["_color"] = (fill, ink)
        # Custom (student-entered) classes carry exact minutes in _span; OBU sections use the time string.
        days, span = parse_days(course["days"]), course.get("_span") or parse_time(course["time"])
        if not days or not span:
            unscheduled.append(course)
            continue
        placed.append(course)
        for day in days:
            by_day[day].append({"course": course, "start": span[0], "end": span[1], "fill": fill, "ink": ink})
    if not any(c["department"] == "CHAP" for c in courses):
        for day in CHAPEL["days"]:
            by_day[day].append({"course": None, "start": CHAPEL["start"], "end": CHAPEL["end"]})

    conflicts = []
    for day, events in by_day.items():
        for i, a in enumerate(events):
            for b in events[i + 1:]:
                if a["start"] < b["end"] and b["start"] < a["end"]:
                    a["conflict"] = b["conflict"] = True
                    names = [course_label(e["course"]) if e["course"] else "Chapel" for e in (a, b)]
                    conflicts.append(f"{DAY_LABELS[day].title()}: {names[0]} overlaps {names[1]} "
                                     f"({short_clock(max(a['start'], b['start']))}–{short_clock(min(a['end'], b['end']))})")
        layout(events)

    days_shown = [d for d in DAY_ORDER if d != "S" or by_day["S"]]
    starts = [e["start"] for d in days_shown for e in by_day[d]]
    ends = [e["end"] for d in days_shown for e in by_day[d]]
    first_hour = min([8] + [s // 60 for s in starts])
    last_hour = max([17] + [-(-e // 60) for e in ends])
    grid_start = first_hour * 60
    height = (last_hour - first_hour) * HOUR_PX

    # Calendar blocks mirror the website: code + time on the first line, then the title on as many
    # whole lines as the block has room for (ellipsized), side-by-side conflicts show the code only.
    def block(event: dict) -> str:
        top = (event["start"] - grid_start) / 60 * HOUR_PX
        size = max((event["end"] - event["start"]) / 60 * HOUR_PX, 18)
        width = 100 / event["cols"]
        style = f"top:{top:.1f}px;height:{size:.1f}px;left:calc({event['col'] * width:.3f}% + 2px);width:calc({width:.3f}% - 4px);"
        conflict = " conflict" if event.get("conflict") else ""
        if event["course"] is None:
            return f'<div class="ev chapel{conflict}" style="{style}"><b>Chapel</b></div>'
        course = event["course"]
        style += f'background:{event["fill"]};border-color:{event["ink"]};color:{event["ink"]};'
        if event["cols"] > 1:
            return f'<div class="ev narrow{conflict}" style="{style}"><b>{escape(course_label(course))}</b></div>'
        lines = max(1, int((size - 6 - 13) // 11.5))
        return (f'<div class="ev{conflict}" style="{style}"><div class="top"><b>{escape(course_label(course))}</b>'
                f'<span class="t">{short_clock(event["start"])}–{short_clock(event["end"])}</span></div>'
                f'<span class="title" style="-webkit-line-clamp:{lines};line-clamp:{lines}">{escape(course["title"])}</span></div>')

    hour_rows = "".join(
        f'<div class="hour" style="top:{(h - first_hour) * HOUR_PX}px">{short_clock(h * 60)}</div>'
        for h in range(first_hour, last_hour))
    lines = "".join(f'<div class="line" style="top:{(h - first_hour) * HOUR_PX}px"></div>'
                    for h in range(first_hour, last_hour))
    columns = "".join(
        f'<div class="day"><div class="dayhead">{DAY_LABELS[d].title()[:3]}</div>'
        f'<div class="daybody" style="height:{height}px">{lines}{"".join(block(e) for e in by_day[d])}</div></div>'
        for d in days_shown)

    rows = []
    for course in courses:
        fill, ink = course["_color"]
        days, span = parse_days(course["days"]), course.get("_span") or parse_time(course["time"])
        when_days = day_text(days) if days and span else escape(course["days"])
        when_time = f"{short_clock(span[0])}–{short_clock(span[1])}" if days and span else escape(course["time"])
        note = '<div class="muted">Not on the calendar</div>' if course in unscheduled else ""
        hours = "" if course.get("credit_hours") is None else f"{course['credit_hours']:g}"
        rows.append(
            f'<tr><td><span class="swatch" style="background:{fill};border-color:{ink}"></span></td>'
            f'<td>{escape(course["sequence"])}</td><td><b>{escape(course_label(course))}</b></td>'
            f'<td class="title">{escape(course["title"])}{note}</td><td>{escape(course["time_slot"])}</td>'
            f'<td>{when_days}</td><td>{when_time}</td><td>{hours}</td></tr>')
    known_hours = [c["credit_hours"] for c in courses if c.get("credit_hours") is not None]
    total_hours = sum(known_hours)
    hours_text = f"{total_hours:g}" + ("" if len(known_hours) == len(courses) else " + unknown")

    notes = [f"<li><b>{escape(course_label(c))}</b> (seq {escape(c['sequence'])}): {escape(n)}</li>"
             for c in courses for n in c.get("notes", [])]
    warnings = "".join(f"<li>{escape(c)}</li>" for c in conflicts)
    snapshot_text = datetime.fromisoformat(sections["snapshot_at"]).strftime("%b %d, %Y").replace(" 0", " ")
    site = config.site_url()
    # Optional pointer back to the live planner (PLANNER_SITE_URL), shown without the scheme.
    live = f' Live: <a href="{escape(site)}">{escape(re.sub(r"^https?://", "", site))}</a>' if site else ""
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pre-Registration Schedule - {escape(sections['semester_label'])}</title>
<style>
@page{{size:letter;margin:0.45in}}
*{{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
body{{font-family:"Public Sans",Inter,"Segoe UI",Helvetica,Arial,sans-serif;color:#202124;max-width:7.6in;margin:0 auto;padding:16px;background:#fff;font-size:12px;line-height:1.35}}
.head{{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin:0 0 10px}}
h1{{margin:0;font-size:20px;letter-spacing:-.02em}}
.sem{{font-size:15px;font-weight:700}} .sem span{{font-weight:400;color:#62656a;font-size:12px;margin-left:6px}}
.ident{{display:flex;justify-content:space-between;gap:24px;margin:0 0 12px;font-size:12px;color:#62656a;text-transform:uppercase;letter-spacing:.04em}}
.ident span{{display:inline-block;border-bottom:1px solid #202124;min-width:2.7in;padding:0 6px;color:#202124;text-transform:none;letter-spacing:0;font-size:13px}}
.ident .id span{{min-width:1.4in}}
table{{width:100%;border-collapse:collapse}}
th{{padding:4px 5px;border-bottom:2px solid #202124;color:#62656a;font-size:9.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;text-align:left}}
td{{padding:5px;border-bottom:1px solid #dedede;height:25px;vertical-align:top}}
tfoot td{{border-bottom:0;font-weight:600}}
.swatch{{display:inline-block;width:11px;height:11px;margin-top:2px;border:2px solid;border-radius:3px}}
.muted{{color:#62656a;font-size:10px}}
.reminder{{margin:8px 0 14px;color:#62656a}}
.cal{{display:flex;border:1px solid #dedede;border-radius:8px;overflow:hidden}}
.hours{{width:44px;flex:none}}
.dayhead{{height:22px;border-bottom:1px solid #dedede;background:#f1f3f4;text-align:center;font-size:11px;font-weight:600;line-height:22px}}
.hourbody,.daybody{{position:relative}}
.hour{{position:absolute;right:5px;color:#62656a;font-size:9px}}
.day{{flex:1;border-left:1px solid #dedede;min-width:0}}
.line{{position:absolute;left:0;right:0;border-top:1px solid #eee}}
.ev{{position:absolute;display:flex;flex-direction:column;justify-content:center;gap:1px;padding:2px 4px;border:1.5px solid;border-radius:4px;font-size:9.5px;line-height:1.2;overflow:hidden}}
.ev .top{{display:flex;gap:5px;align-items:baseline;min-width:0}}
.ev b{{font-size:10.5px;white-space:nowrap;flex-shrink:0}}
.ev .t{{min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;opacity:.85}}
.ev .title{{display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:break-word}}
.ev.narrow b{{white-space:normal;line-height:1.15}}
.chapel{{align-items:center;border-color:#999;background:repeating-linear-gradient(45deg,#f1f1f1,#f1f1f1 5px,#e4e4e4 5px,#e4e4e4 10px);color:#444}}
.conflict{{outline:2.5px dashed #a52834;outline-offset:-1px}}
.warn{{border:1.5px solid #a52834;border-radius:6px;color:#a52834;padding:6px 10px;margin:0 0 12px}}
.warn ul,.foot ul{{margin:4px 0 0 18px;padding:0}}
.foot{{font-size:10px;color:#62656a;margin-top:10px}}
.foot a{{color:#174ea6}}
@media print{{body{{padding:0;zoom:.86}}}}
</style></head><body>
<div class="head"><h1>Pre-Registration Schedule</h1>
<div class="sem">{escape(sections['semester_label'])}<span>{hours_text} credit hours</span></div></div>
<div class="ident"><div>Name <span>{escape(student_name) or "&nbsp;"}</span></div>
<div class="id">I.D. # <span>{escape(student_id) or "&nbsp;"}</span></div></div>
<table><thead><tr><th style="width:4%"></th><th style="width:8%">Seq #</th><th style="width:12%">Course #</th><th>Course title</th>
<th style="width:8%">Time slot</th><th style="width:8%">Days</th><th style="width:16%">Time</th><th style="width:7%">Hours</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
<tfoot><tr><td colspan="7" style="text-align:right">Total credit hours</td><td>{hours_text}</td></tr></tfoot></table>
<div class="reminder">Reminder: 1. Chapel (10 a.m., Tuesday)&nbsp;&nbsp; 2. Lunch</div>
{f'<div class="warn"><b>Time conflicts:</b><ul>{warnings}</ul></div>' if conflicts else ""}
<div class="cal"><div class="hours"><div class="dayhead"></div><div class="hourbody" style="height:{height}px">{hour_rows}</div></div>{columns}</div>
<div class="foot">
{f"<b>Section notes:</b><ul>{''.join(notes)}</ul>" if notes else ""}
<p>Schedule updated {escape(snapshot_text)}.{live}</p>
</div></body></html>"""
    return {
        "html": page,
        "suggested_filename": f"pre-registration-{re.sub(r'[^a-z0-9]+', '-', sections['semester_label'].lower()).strip('-')}.html",
        "semester": sections["semester"], "semester_label": sections["semester_label"],
        "sections": [{"sequence": c["sequence"], "course": course_label(c), "title": c["title"],
                      "days": c["days"], "time": c["time"], "color": c["_color"][0],
                      "credit_hours": c.get("credit_hours"), "on_calendar": c in placed} for c in courses],
        "total_credit_hours": total_hours, "credit_hours_complete": len(known_hours) == len(courses),
        "conflicts": conflicts,
    }
