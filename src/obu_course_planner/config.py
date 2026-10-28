"""Deployment settings, read from the environment so the code names no particular site or server.

See .env.example for every variable. Values are read on each use, so tests and reloads see changes.
"""
from __future__ import annotations

import os

DEFAULT_ORIGINS = ("http://localhost:4321", "http://127.0.0.1:4321")  # local Astro dev server
DEFAULT_USER_AGENT = "obu-course-planner (+https://github.com/kickofline/obu-course-planner)"


def allowed_origins() -> frozenset[str]:
    """Browser origins allowed to call the API (CORS). Comma-separated PLANNER_ALLOWED_ORIGINS."""
    raw = os.environ.get("PLANNER_ALLOWED_ORIGINS", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return frozenset(origins or DEFAULT_ORIGINS)


def site_url() -> str:
    """Public planner page, printed on worksheets and shown at the API root. Optional."""
    return os.environ.get("PLANNER_SITE_URL", "").strip()


def user_agent() -> str:
    """User-Agent for requests to OBU's schedule site; set OBU_REFRESH_USER_AGENT to add contact details."""
    return os.environ.get("OBU_REFRESH_USER_AGENT", "").strip() or DEFAULT_USER_AGENT
