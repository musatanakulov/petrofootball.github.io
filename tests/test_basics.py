"""Базовые вещи: нормализация названий, окна голосования, проверка прав администратора."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import main
from conftest import ADMIN, TZ, finish, sign_init_data, tg


def test_norm_ignores_case_spaces_underscores_and_kazakh_letters():
    assert main.norm("КӨКЖАЛ") == main.norm(" көкжал ")
    assert main.norm("FC_RSB") == main.norm("FC  RSB")
    assert main.norm("ӘҒҚҢӨҰҮҺІ") == "әғқңөұүһі"


def test_normalize_round_name():
    cases = {"Первый тур": "Тур 1", "первый  ТУР": "Тур 1", "Второй тур": "Тур 2", "1 тур": "Тур 1",
             "13-й тур": "Тур 13", "Тур 5": "Тур 5", "тур 7": "Тур 7", "Финал": "Финал"}
    for raw, expected in cases.items():
        assert main.normalize_round_name(raw) == expected


def _window(day, hour):
    opens, closes = main.voting_window(datetime(2026, 11, day, hour, tzinfo=TZ).astimezone(timezone.utc))
    return opens.strftime("%a %d.%m"), closes.strftime("%a %d.%m")


def test_voting_window_always_monday_to_tuesday_24h():
    assert _window(15, 23) == ("Mon 16.11", "Tue 17.11")   # завершили в воскресенье -> завтра
    assert _window(16, 10) == ("Mon 23.11", "Tue 24.11")   # в понедельник -> через неделю
    assert _window(17, 1) == ("Mon 23.11", "Tue 24.11")    # во вторник
    assert _window(14, 20) == ("Mon 16.11", "Tue 17.11")   # в субботу
    opens, closes = main.voting_window(datetime(2026, 11, 11, tzinfo=TZ).astimezone(timezone.utc))
    assert closes - opens == timedelta(hours=24)


def test_auth_check_variants(client):
    check = lambda headers=None: client.get("/api/auth/check", headers=headers or {}).json()["is_admin"]  # noqa: E731
    assert check() is False
    assert check(tg(1001)) is True
    assert check(tg(999)) is False                                   # настоящий Telegram, но не админ
    assert check(tg(1001, token="1:WRONG")) is False                 # подделанная подпись
    assert check(tg(1001, age=10 * 86400)) is False                  # просроченная подпись


def test_admin_endpoints_require_signature(client, mini):
    mid = mini["matches"]["AB"]
    body = {"home_score": 1, "away_score": 0, "events": []}
    assert client.post(f"/api/matches/{mid}/result", json=body).status_code == 403
    assert client.post(f"/api/matches/{mid}/result", json=body, headers={"telegram_user_id": "1001"}).status_code == 403  # старый подделываемый заголовок
    assert client.post(f"/api/matches/{mid}/result", json=body, headers=tg(999)).status_code == 403
    assert finish(client, mid, 1, 0).status_code == 200


def test_dev_admin_only_for_direct_local_requests(monkeypatch):
    monkeypatch.setattr(main, "ALLOW_DEV_ADMIN", True)
    direct = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    via_tunnel = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"x-forwarded-for": "5.5.5.5"})
    cloudflare = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"cf-connecting-ip": "5.5.5.5"})
    remote = SimpleNamespace(client=SimpleNamespace(host="8.8.8.8"), headers={})
    assert main.is_admin_request(direct, None) is True
    for req in (via_tunnel, cloudflare, remote):
        assert main.is_admin_request(req, None) is False   # из интернета без подписи Telegram админом не стать


def test_frontend_files_served_and_secrets_not(client):
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    for secret in ("/main.py", "/.env", "/football_tournament.db", "/config.py"):
        assert client.get(secret).status_code == 404
