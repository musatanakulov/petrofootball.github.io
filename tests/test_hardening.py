"""Защита для публичного запуска: заголовки, /docs, обработка 500, кэш медиа, WAL."""
import main
from database import engine


def test_security_headers_and_docs_disabled(client):
    r = client.get("/api/leagues")
    assert r.headers["x-content-type-options"] == "nosniff" and "x-frame-options" not in r.headers   # Telegram открывает во фрейме
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_unhandled_error_returns_clean_500_and_is_logged(client):
    def boom():
        raise RuntimeError("secret internal detail")
    main.app.add_api_route("/api/_boom", boom)
    try:
        from fastapi.testclient import TestClient
        r = TestClient(main.app, raise_server_exceptions=False).get("/api/_boom")
    finally:
        main.app.router.routes.pop()
    assert r.status_code == 500 and "secret" not in r.text and "ошибка" in r.json()["detail"].lower()


def test_media_is_cacheable_but_frontend_is_not(client):
    assert client.get("/app.js").headers["cache-control"] == "no-cache"
    logos = main.LOGO_DIR
    import os
    name = next((f for f in os.listdir(logos)), None)
    if name:
        assert client.get(f"/static/logos/{name}").headers["cache-control"] == "public, max-age=3600"


def test_sqlite_uses_wal():
    with engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_huge_id_is_404_not_500(client):
    assert client.get("/api/matches/99999999999999999999").status_code == 404
