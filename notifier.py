"""Уведомления бота: напоминания о матчах любимой команды и открытие голосования за «Игрока тура».

Бот (bot.py) раз в минуту вызывает run_once(). Что и кому отправлено, хранится в таблице sent_notifications,
поэтому одно и то же сообщение никому не уходит дважды (даже после перезапуска бота).

  • за сутки (от 24 до 2 часов до начала) и за 2 часа (последние 2 часа) — тем, у кого эта команда «моя»;
  • в понедельник, когда голосование открылось, — всем подписчикам с включёнными уведомлениями.
"""
import time
from datetime import timezone, timedelta

from sqlalchemy.orm import Session

import main
from database import League, Match, SentNotification, Subscriber

SOON = timedelta(hours=24)
VERY_SOON = timedelta(hours=2)

KK_MONTHS = ["қаңтар", "ақпан", "наурыз", "сәуір", "мамыр", "маусым", "шілде", "тамыз", "қыркүйек", "қазан", "қараша", "желтоқсан"]


class Blocked(Exception):
    """Пользователь заблокировал бота или удалил чат — слать бессмысленно."""


def _when(starts_at, lang):
    if lang == "kk":
        return f"{starts_at.day} {KK_MONTHS[starts_at.month - 1]}, {starts_at:%H:%M}"
    return f"{starts_at.day} {main.RU_MONTHS[starts_at.month - 1]}, {starts_at:%H:%M}"


def match_text(lang, kind, home, away, starts_at):
    when = _when(starts_at, lang)
    if lang == "kk":
        head = "⚽ Ертең ойын" if kind == "match24" else "⚽ 2 сағаттан кейін ойын басталады"
        return f"{head}\n{home} — {away}\n🕒 {when}"
    head = "⚽ Завтра матч вашей команды" if kind == "match24" else "⚽ Через 2 часа матч вашей команды"
    return f"{head}\n{home} — {away}\n🕒 {when}"


def vote_text(lang, league, round_name):
    if lang == "kk":
        return (f"🗳 «Тур ойыншысы» бойынша дауыс беру ашылды!\n{league}, {round_name}\n"
                "Дауыс беру сейсенбі күні 00:00-ге дейін.")
    return f"🗳 Открылось голосование за «Игрока тура»!\n{league}, {round_name}\nГолосовать можно до вторника 00:00."


def due_notifications(db: Session, now=None) -> list:
    """Список ещё не отправленных уведомлений: [(подписчик, ключ, текст)]."""
    now = now or main.now_utc()
    subs = [s for s in db.query(Subscriber).filter(Subscriber.notifications == True, Subscriber.blocked == False).all()  # noqa: E712
            if s.chat_id and s.chat_id > 0]                     # chat_id <= 0 — локальная отладка без Telegram
    if not subs:
        return []
    sent = {(r.subscriber_id, r.key) for r in db.query(SentNotification).all()}
    out = []

    def add(sub, key, text):
        if (sub.id, key) not in sent:
            out.append((sub, key, text))

    # 1) голосование за «Игрока тура» открылось — всем подписчикам
    for league in db.query(League).all():
        for poll in main.build_league_polls(db, league.id, None):
            if poll["status"] == "active":
                key = f"vote:{league.id}:{poll['round']}"
                for s in subs:
                    add(s, key, vote_text(s.lang or "ru", league.name, poll["round"]))

    # 2) напоминания о матчах любимой команды
    favorites = {}
    for s in subs:
        if s.favorite_team_id:
            favorites.setdefault(s.favorite_team_id, []).append(s)
    if favorites:
        for m in db.query(Match).filter(Match.status == "scheduled", Match.annulled == False, Match.starts_at.isnot(None)).all():  # noqa: E712
            starts_utc = m.starts_at.replace(tzinfo=main.TOURNAMENT_TZ).astimezone(timezone.utc)
            until = starts_utc - now
            if until <= timedelta(0) or until > SOON:
                continue
            kind = "match2" if until <= VERY_SOON else "match24"
            # время в ключе: если матч перенесли, напоминание придёт заново
            key = f"{kind}:{m.id}:{m.starts_at:%Y%m%d%H%M}"
            for tid in (m.home_team_id, m.away_team_id):
                for s in favorites.get(tid, []):
                    add(s, key, match_text(s.lang or "ru", kind, m.home_team.name, m.away_team.name, m.starts_at))
    return out


def run_once(db: Session, send, now=None, pause: float = 0.05) -> int:
    """Отправляет всё, что пора. send(chat_id, text) может бросить Blocked. Возвращает число отправленных."""
    sent_count = 0
    for sub, key, text in due_notifications(db, now):
        try:
            send(sub.chat_id, text)
        except Blocked:
            sub.blocked = True          # больше не пишем, пока пользователь сам не вернётся (/start)
            db.commit()
            continue
        except Exception as e:           # временный сбой — попробуем в следующий раз
            print(f"[notify] не отправлено {sub.chat_id}: {e}")
            continue
        db.add(SentNotification(subscriber_id=sub.id, key=key))
        db.commit()
        sent_count += 1
        if pause:
            time.sleep(pause)            # лимит Telegram — около 30 сообщений в секунду
    return sent_count
