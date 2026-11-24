from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import accounts, page_preview
from app.main import RevalidatedStatic, WEB_DIR


def test_preview_survives_prerender_and_returns_to_requested_page(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCOUNTS_ENABLED", "1")
    monkeypatch.delenv("ACCOUNTS_ALLOWED_EMAILS", raising=False)
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "workspace.db")
    monkeypatch.setattr(accounts, "_conn", None)
    app = FastAPI()
    app.include_router(accounts.router)
    app.mount("/", RevalidatedStatic(directory=str(WEB_DIR), html=True))
    client = TestClient(app, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=False)

    first = client.get("/cpi.html")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, no-store"
    assert page_preview.COOKIE in client.cookies
    assert client.get("/cpi.html?ref=newsletter").status_code == 200
    assert client.get("/gdp.html").status_code == 200
    assert client.get("/labour.html").status_code == 200

    target = "/us-treasury.html?maturity=10y&ref=test"
    blocked = client.get(target)
    assert blocked.status_code == 302
    assert blocked.headers["cache-control"] == "private, no-store"
    location = urlsplit(blocked.headers["location"])
    assert location.path == "/signin.html"
    assert parse_qs(location.query) == {"reason": ["page-preview"], "returnTo": [target]}
    assert client.get("/cpi.html").status_code == 200
    assert client.get("/manual.html").status_code == 200
    assert client.get(target, headers={"User-Agent": "Googlebot"}).status_code == 200

    link = accounts.issue_link("reader@example.com", target, "http://testserver/")
    token = parse_qs(urlsplit(link).query)["token"][0]
    session = client.post("/api/v1/account/session", json={"token": token})
    assert session.status_code == 200
    assert session.json()["returnTo"] == target
    assert client.get(target).status_code == 200
    assert client.post("/api/v1/account/signout").status_code == 200
    assert client.get(target).status_code == 302

    monkeypatch.setenv("ACCOUNTS_ALLOWED_EMAILS", "invited@example.com")
    assert client.get(target).status_code == 200


def test_concurrent_requests_share_the_remaining_allowance(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "workspace.db")
    _, cookie = page_preview._open_preview(None, "cpi.html")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda page: page_preview._open_preview(cookie, page),
                                ["boj.html", "labour.html", "gdp.html"]))
    assert sum(allowed for allowed, _ in results) == 2
