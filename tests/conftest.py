"""Общие настройки тестов: временная база, подписанные Telegram-заголовки, фиксированные часы, помощники.

Запуск:  py -m pytest -q
Тесты НЕ трогают рабочую базу: DATABASE_URL указывает на временный файл, подпись Telegram — на тестовый токен.
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ВАЖНО: переменные окружения задаём до импорта database/main/config
_TMP = tempfile.mkdtemp(prefix="petrofoot-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["BOT_TOKEN"] = "123456:TEST-TOKEN-for-unit-tests"
os.environ["ADMIN_TELEGRAM_IDS"] = "1001"
os.environ["ALLOW_DEV_ADMIN"] = "0"
os.environ["TZ_OFFSET_HOURS"] = "5"
os.environ["BACKUP_DIR"] = os.path.join(_TMP, "backups")
os.environ["BACKUP_SCHEDULER"] = "0"   # в тестах фоновый поток копий не нужен

from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
from database import Base, League, Match, Player, SessionLocal, Team  # noqa: E402

TZ = timezone(timedelta(hours=5))
ADMIN_ID = 1001


def sign_init_data(user_id: int, token: str | None = None, age: int = 0) -> str:
    """Строка initData, подписанная так же, как это делает Telegram."""
    fields = {"auth_date": str(int(time.time()) - age), "user": json.dumps({"id": user_id, "first_name": "Т"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", (token or os.environ["BOT_TOKEN"]).encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def tg(user_id: int, **kw) -> dict:
    return {"X-Telegram-Init-Data": sign_init_data(user_id, **kw)}


ADMIN = lambda: tg(ADMIN_ID)  # noqa: E731


@pytest.fixture()
def client():
    """Свежая пустая база + три лиги на каждый тест."""
    Base.metadata.drop_all(bind=database.engine)
    with TestClient(main.app) as c:   # startup создаёт таблицы и лиги
        yield c


@pytest.fixture()
def clock(monkeypatch):
    """Управляемое «сейчас»: clock(2026, 11, 11, 12) — среда 12:00 по времени турнира (UTC+5)."""
    def set_time(y, m, d, h=12, mi=0, s=0):
        value = datetime(y, m, d, h, mi, s, tzinfo=TZ).astimezone(timezone.utc)
        monkeypatch.setattr(main, "now_utc", lambda: value)
    set_time(2026, 11, 11)
    return set_time


@pytest.fixture()
def mini(client):
    """Мини-лига: 4 команды, по 3 игрока, 3 тура «каждый с каждым». Возвращает словарь id."""
    db = SessionLocal()
    league = db.query(League).filter(League.name == "Первая лига").first()
    league_id = league.id
    teams, players = {}, {}
    for key in "ABCD":
        t = Team(name=f"Команда {key}", league_id=league_id)
        db.add(t); db.commit(); teams[key] = t.id
        for i in (1, 2, 3):
            p = Player(name=f"Игрок {key}{i}", number=i, team_id=t.id)
            db.add(p); db.commit(); players[f"{key}{i}"] = p.id
    pairs = {1: [("A", "B"), ("C", "D")], 2: [("A", "C"), ("B", "D")], 3: [("A", "D"), ("B", "C")]}
    matches = {}
    for rnd, plist in pairs.items():
        for h, a in plist:
            m = Match(league_id=league_id, round_name=f"Тур {rnd}", match_date="", match_time="", status="scheduled",
                      home_team_id=teams[h], away_team_id=teams[a])
            db.add(m); db.commit(); matches[f"{h}{a}"] = m.id
    db.close()
    return {"league": league_id, "teams": teams, "players": players, "matches": matches}


def finish(client, match_id, home=0, away=0, events=None, motm=None, technical=False):
    """Внести результат от имени админа."""
    return client.post(f"/api/matches/{match_id}/result", headers=ADMIN(), json={
        "home_score": home, "away_score": away, "man_of_the_match_id": motm, "events": events or [], "technical": technical})


def ev(player_id, team_id, kind, minute=None):
    return {"player_id": player_id, "team_id": team_id, "event_type": kind, "minute": minute}


def player_stats(client):
    return {p["id"]: p["stats"] for lg in client.get("/api/leagues").json() for t in lg["teams"] for p in t["players"]}
