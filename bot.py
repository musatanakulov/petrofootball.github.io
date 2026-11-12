"""Telegram-бот, открывающий Mini App турнира и присылающий уведомления.

Работает только на стандартной библиотеке Python (long polling Bot API), без aiogram.
Токен берётся из .env (BOT_TOKEN), адрес приложения — из переменной окружения / .env (WEBAPP_URL, обязательно https).

Команды:  /start — открыть турнир и включить уведомления;  /notify on|off — включить/выключить уведомления.
Уведомления (notifier.py): напоминания о матчах «моей команды» и открытие голосования за «Игрока тура».
"""
import html
import json
import sys
import time
import urllib.error
import urllib.request

from config import BOT_TOKEN, WEBAPP_URL

if not BOT_TOKEN:
    sys.exit("BOT_TOKEN не задан. Впишите токен от @BotFather в файл .env (см. .env.example)")
if not WEBAPP_URL.startswith("https://"):
    sys.exit(f"WEBAPP_URL должен начинаться с https:// (Telegram не открывает Mini App по http). Сейчас: {WEBAPP_URL!r}")

import notifier  # noqa: E402  (после проверок, чтобы быстро падать без токена)
from database import SessionLocal, Subscriber, init_db  # noqa: E402

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
NOTIFY_EVERY_SECONDS = 60


class TelegramError(RuntimeError):
    def __init__(self, method, code, body):
        super().__init__(f"{method}: HTTP {code} {body[:200]}")  # токен в сообщение не попадает
        self.code = code


def call(method: str, payload: dict | None = None, timeout: int = 40) -> dict:
    req = urllib.request.Request(
        f"{API}/{method}",
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise TelegramError(method, e.code, e.read().decode("utf-8", "replace")) from None


OPEN_BUTTON = {"inline_keyboard": [[{"text": "🏆 Открыть турнир", "web_app": {"url": WEBAPP_URL}}]]}


def send_message(chat_id: int, text: str, button: bool = True) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if button:
        payload["reply_markup"] = OPEN_BUTTON
    call("sendMessage", payload)


def notify_send(chat_id: int, text: str) -> None:
    """Отправка для notifier: 403/400 «чат не найден» = пользователь заблокировал бота."""
    try:
        send_message(chat_id, html.escape(text), button=True)
    except TelegramError as e:
        if e.code in (403, 400):
            raise notifier.Blocked() from None
        raise


def upsert_subscriber(user: dict, chat_id: int) -> Subscriber:
    db = SessionLocal()
    try:
        sub = db.query(Subscriber).filter(Subscriber.tg_user_id == user["id"]).first()
        if not sub:
            lang = "kk" if str(user.get("language_code", "")).startswith("kk") else "ru"
            sub = Subscriber(tg_user_id=user["id"], chat_id=chat_id, first_name=(user.get("first_name") or "")[:100], lang=lang)
            db.add(sub)
        sub.chat_id, sub.blocked = chat_id, False   # написал боту — значит снова можно слать
        db.commit()
        db.refresh(sub)
        db.expunge(sub)
        return sub
    finally:
        db.close()


def set_notifications(user_id: int, on: bool) -> None:
    db = SessionLocal()
    try:
        sub = db.query(Subscriber).filter(Subscriber.tg_user_id == user_id).first()
        if sub:
            sub.notifications = on
            db.commit()
    finally:
        db.close()


def handle_message(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    user, chat_id = msg.get("from") or {}, msg["chat"]["id"]
    if not user.get("id"):
        return
    if text.startswith("/start") or text.startswith("/open"):
        sub = upsert_subscriber(user, chat_id)
        name = html.escape(user.get("first_name") or "друг")
        if sub.lang == "kk":
            send_message(chat_id, f"Сәлем, <b>{name}</b>! 👋\n\nКесте, нәтижелер, кестелер, статистика және «Тур ойыншысы» дауыс беруі — төмендегі батырма арқылы.\n"
                                  "🔔 Хабарламалар қосулы. Өшіру: /notify off")
        else:
            send_message(chat_id, f"Привет, <b>{name}</b>! 👋\n\nДобро пожаловать в официальный менеджер футбольного турнира.\n"
                                  "Нажмите кнопку ниже, чтобы открыть расписание, результаты, таблицы лиг, статистику и голосование за игрока тура.\n"
                                  "🔔 Уведомления включены (о матчах вашей команды и голосовании). Выключить: /notify off")
    elif text.startswith("/notify"):
        upsert_subscriber(user, chat_id)
        arg = text.split()[1].lower() if len(text.split()) > 1 else ""
        if arg in ("on", "off"):
            set_notifications(user["id"], arg == "on")
            send_message(chat_id, "🔔 Уведомления включены." if arg == "on" else "🔕 Уведомления выключены. Включить: /notify on", button=False)
        else:
            send_message(chat_id, "Напишите /notify on, чтобы включить уведомления, или /notify off, чтобы выключить.", button=False)


def run_notifier() -> None:
    db = SessionLocal()
    try:
        n = notifier.run_once(db, notify_send)
        if n:
            print(f"[notify] отправлено уведомлений: {n}")
    except Exception as e:  # рассылка не должна ронять бота
        print(f"[notify] ОШИБКА: {e!r}")
    finally:
        db.close()


def main() -> None:
    init_db()  # таблицы подписчиков создаются и здесь, если бот запущен раньше сервера
    me = call("getMe")["result"]
    print(f"Бот @{me['username']} запущен. Mini App: {WEBAPP_URL}", flush=True)
    call("deleteWebhook")  # long polling не работает при установленном webhook
    # Кнопка меню рядом с полем ввода: открывает Mini App во всех чатах с ботом
    call("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Турнир", "web_app": {"url": WEBAPP_URL}}})
    print("Кнопка меню «Турнир» установлена. Ожидаю сообщений (Ctrl+C — остановить)...", flush=True)

    offset, last_tick = None, 0.0
    while True:
        if time.time() - last_tick >= NOTIFY_EVERY_SECONDS:
            run_notifier()
            last_tick = time.time()
        try:
            updates = call("getUpdates", {"timeout": 20, "offset": offset, "allowed_updates": ["message"]}, timeout=35)["result"]
        except (RuntimeError, urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[warn] {e}; повтор через 5 с", flush=True)
            time.sleep(5)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            if upd.get("message"):
                try:
                    handle_message(upd["message"])
                except (RuntimeError, urllib.error.URLError) as e:
                    print(f"[warn] {e}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Бот остановлен.")
