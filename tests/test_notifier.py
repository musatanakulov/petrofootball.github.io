"""«Моя команда», настройки пользователя и уведомления бота."""
from conftest import ADMIN, finish, tg
import database
import main
import notifier
from database import SessionLocal, Subscriber


def settings(client, user, **body):
    return client.post("/api/me/settings", headers=tg(user), json=body)


# ---------------- настройки: моя команда, язык, уведомления ----------------

def test_me_requires_telegram_signature(client, mini):
    assert client.get("/api/me").json() == {"identified": False, "favorite_team_id": None, "notifications": True, "lang": None}
    assert client.post("/api/me/settings", json={"lang": "kk"}).status_code == 403
    assert client.post("/api/me/settings", headers=tg(7, token="1:WRONG"), json={"lang": "kk"}).status_code == 403


def test_favorite_team_language_and_notifications_roundtrip(client, mini):
    team = mini["teams"]["B"]
    r = settings(client, 2001, favorite_team_id=team, lang="kk", notifications=False)
    assert r.status_code == 200 and r.json() == {"identified": True, "favorite_team_id": team, "notifications": False, "lang": "kk"}
    assert client.get("/api/me", headers=tg(2001)).json()["favorite_team_id"] == team
    assert client.get("/api/me", headers=tg(2002)).json()["favorite_team_id"] is None     # у каждого свои настройки
    assert settings(client, 2001, clear_favorite=True).json()["favorite_team_id"] is None
    assert settings(client, 2001, lang="de").status_code == 400
    assert settings(client, 2001, favorite_team_id=99999).status_code == 404


# ---------------- уведомления ----------------

def make_sub(user=2001, team=None, lang="ru", notifications=True):
    db = SessionLocal()
    db.add(Subscriber(tg_user_id=user, chat_id=user, first_name="Т", favorite_team_id=team, lang=lang, notifications=notifications))
    db.commit(); db.close()


def schedule(client, mid, date, time):
    assert client.post("/api/matches/schedule", headers=ADMIN(), json={"items": [{"match_id": mid, "match_date": date, "match_time": time}]}).status_code == 200


class Outbox:
    def __init__(self, fail_with=None):
        self.messages, self.fail_with = [], fail_with

    def __call__(self, chat_id, text):
        if self.fail_with:
            raise self.fail_with
        self.messages.append((chat_id, text))


def run(outbox):
    db = SessionLocal()
    try:
        return notifier.run_once(db, outbox, pause=0)
    finally:
        db.close()


def test_match_reminders_24h_and_2h_only_for_the_favorite_team(client, mini, clock):
    make_sub(2001, team=mini["teams"]["A"]); make_sub(2002, team=mini["teams"]["C"])
    schedule(client, mini["matches"]["AB"], "2026-11-12", "15:00")        # матч A — B
    out = Outbox()
    clock(2026, 11, 11, 10)                                               # за 29 часов — рано
    assert run(out) == 0
    clock(2026, 11, 11, 16)                                               # за 23 часа
    assert run(out) == 1 and out.messages[0][0] == 2001 and "Завтра матч" in out.messages[0][1]
    assert "Команда A — Команда B" in out.messages[0][1] and "12 ноября, 15:00" in out.messages[0][1]
    assert run(out) == 0                                                  # повторно то же не шлём
    clock(2026, 11, 12, 13, 30)                                           # за 1.5 часа
    assert run(out) == 1 and "Через 2 часа" in out.messages[1][1]
    clock(2026, 11, 12, 15, 5)                                            # матч уже начался
    assert run(out) == 0
    assert all(chat == 2001 for chat, _ in out.messages)                  # подписчик с командой C ничего не получил


def test_rescheduled_match_notifies_again(client, mini, clock):
    make_sub(2001, team=mini["teams"]["A"])
    out = Outbox()
    schedule(client, mini["matches"]["AB"], "2026-11-12", "15:00")
    clock(2026, 11, 11, 16); assert run(out) == 1
    schedule(client, mini["matches"]["AB"], "2026-11-12", "15:30")        # перенесли время (всё ещё в пределах суток)
    assert run(out) == 1                                                   # новое напоминание


def test_kazakh_subscriber_gets_kazakh_text(client, mini, clock):
    make_sub(2001, team=mini["teams"]["A"], lang="kk")
    schedule(client, mini["matches"]["AB"], "2026-11-12", "15:00")
    out = Outbox(); clock(2026, 11, 11, 16); run(out)
    assert "Ертең ойын" in out.messages[0][1] and "қараша" in out.messages[0][1]


def test_voting_opened_goes_to_all_subscribers_once(client, mini, clock):
    make_sub(2001); make_sub(2002); make_sub(2003, notifications=False)
    clock(2026, 11, 11)
    finish(client, mini["matches"]["AB"], 1, 0, motm=mini["players"]["A1"])
    finish(client, mini["matches"]["CD"], 0, 0, motm=mini["players"]["D1"])
    out = Outbox()
    clock(2026, 11, 15, 23, 59); assert run(out) == 0                      # ещё не открылось
    clock(2026, 11, 16, 0, 5)
    assert run(out) == 2                                                   # только тем, у кого уведомления включены
    assert {chat for chat, _ in out.messages} == {2001, 2002} and "голосование" in out.messages[0][1]
    assert run(out) == 0                                                   # не дублируем
    clock(2026, 11, 17, 0, 5); assert run(out) == 0                        # закрылось — не рассылаем


def test_blocked_user_is_marked_and_skipped(client, mini, clock):
    make_sub(2001, team=mini["teams"]["A"])
    schedule(client, mini["matches"]["AB"], "2026-11-12", "15:00")
    clock(2026, 11, 11, 16)
    assert run(Outbox(fail_with=notifier.Blocked())) == 0
    db = SessionLocal(); assert db.query(Subscriber).one().blocked is True; db.close()
    out = Outbox(); assert run(out) == 0 and not out.messages              # больше не пытаемся


def test_temporary_failure_is_retried_next_time(client, mini, clock):
    make_sub(2001, team=mini["teams"]["A"])
    schedule(client, mini["matches"]["AB"], "2026-11-12", "15:00")
    clock(2026, 11, 11, 16)
    assert run(Outbox(fail_with=RuntimeError("сеть"))) == 0
    out = Outbox(); assert run(out) == 1                                   # со второй попытки дошло


def test_local_debug_user_is_never_messaged(client, mini, clock):
    make_sub(-1, team=mini["teams"]["A"])                                  # отладочный пользователь без Telegram
    schedule(client, mini["matches"]["AB"], "2026-11-12", "15:00")
    clock(2026, 11, 11, 16)
    assert run(Outbox()) == 0
