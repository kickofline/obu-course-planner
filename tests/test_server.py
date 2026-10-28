"""Real frame boundary: /courses may only be embedded by configured ancestors, the API is never framed."""
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from obu_course_planner.server import FrameAncestorsMiddleware, create_app

TRUSTED = "https://embed.example.edu"
UNTRUSTED = "https://evil.example"


def _built_site(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>planner</title>", encoding="utf-8")
    (tmp_path / "embed.js").write_text("/* loader */", encoding="utf-8")
    return tmp_path


def test_courses_frames_only_trusted_ancestors(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNER_ALLOWED_ORIGINS", TRUSTED)
    app = create_app(_built_site(tmp_path))
    with TestClient(app) as client:
        page = client.get("/courses/")
        assert page.status_code == 200 and "planner" in page.text
        expected = f"frame-ancestors 'self' {TRUSTED}"
        assert page.headers["content-security-policy"] == expected
        # Every asset in the embeddable subtree carries the same policy, not just the page.
        assert client.get("/courses/embed.js").headers["content-security-policy"] == expected
        # An unconfigured origin can never be an ancestor, and spare origins are not smuggled in.
        assert UNTRUSTED not in expected
        # The API is fetched cross-origin, never framed, so it stays free of the iframe policy.
        assert "content-security-policy" not in client.get("/healthz").headers


def test_frame_ancestors_merges_without_weakening_other_csp(monkeypatch):
    monkeypatch.setenv("PLANNER_ALLOWED_ORIGINS", TRUSTED)

    async def page(request):
        return PlainTextResponse("ok", headers={"Content-Security-Policy": "default-src 'none'"})

    inner = Starlette(routes=[Route("/x", page)])
    app = Starlette(routes=[Mount("/courses", app=inner)])
    app.add_middleware(FrameAncestorsMiddleware)
    with TestClient(app) as client:
        csp = client.get("/courses/x").headers["content-security-policy"]
    assert csp == f"default-src 'none'; frame-ancestors 'self' {TRUSTED}"


def test_app_serves_api_when_frontend_not_built(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNER_ALLOWED_ORIGINS", TRUSTED)
    app = create_app(tmp_path / "static")  # build absent: API-only environment
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/").json()["api"] == "/api/planner/v1"
        assert client.get("/courses/").status_code == 404