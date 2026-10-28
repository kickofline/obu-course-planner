"""Deployment settings, read from the environment so the code names no particular site or server.

See .env.example for every variable. Values are read on each use, so tests and reloads see changes.
"""
from __future__ import annotations

import os

# Local development: the Astro dev server (frontend/) and the backend serving the built site at /courses/.
DEFAULT_ORIGINS = ("http://localhost:4321", "http://127.0.0.1:4321",
                   "http://localhost:8000", "http://127.0.0.1:8000")


def allowed_origins() -> frozenset[str]:
    """Browser origins allowed to call the API (CORS) and to embed the UI.

    Comma-separated PLANNER_ALLOWED_ORIGINS. List both the standalone origin (this service, serving
    /courses/) and every page that embeds the planner in an iframe.
    """
    raw = os.environ.get("PLANNER_ALLOWED_ORIGINS", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return frozenset(origins or DEFAULT_ORIGINS)


def frame_ancestors() -> str:
    """Content-Security-Policy ``frame-ancestors`` directive for /courses: this origin plus embedders."""
    return "frame-ancestors 'self' " + " ".join(sorted(allowed_origins()))


def site_url() -> str:
    """Public planner page, printed on worksheets and shown at the API root. Optional."""
    return os.environ.get("PLANNER_SITE_URL", "").strip()
