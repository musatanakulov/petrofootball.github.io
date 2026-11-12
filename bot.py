"""Telegram-бот, открывающий Mini App турнира.

Работает только на стандартной библиотеке Python (long polling Bot API), без aiogram.
Токен берётся из .env (BOT_TOKEN), адрес приложения — из переменной окружения / .env (WEBAPP_URL, обязательно https).
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

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


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
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method}: HTTP {e.code} {body[:200]}") from None  # токен в сообщение не попадает


def send_start(chat_id: int, first_name: str) -> None:
    call("sendMessage", {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "text": (
            f"Привет, <b>{html.escape(first_name or 'друг')}</b>! 👋\n\n"
            "Добро пожаловать в официальный менеджер футбольного турнира.\n"
            "Нажмите кнопку ниже, чтобы открыть расписание, результаты, таблицы лиг, статистику и голосование за игрока тура:"
        ),
        "reply_markup": {"inline_keyboard": [[
            {"text": "🏆 Открыть турнир", "web_app": {"url": WEBAPP_URL}}
        ]]},
    })


def main() -> None:
    me = call("getMe")["result"]
    print(f"Бот @{me['username']} запущен. Mini App: {WEBAPP_URL}")
    call("deleteWebhook")  # long polling не работает при установленном webhook
    # Кнопка меню рядом с полем ввода: открывает Mini App во всех чатах с ботом
    call("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Турнир", "web_app": {"url": WEBAPP_URL}}})
    print("Кнопка меню «Турнир» установлена. Ожидаю сообщений (Ctrl+C — остановить)...")

    offset = None
    while True:
        try:
            updates = call("getUpdates", {"timeout": 30, "offset": offset, "allowed_updates": ["message"]}, timeout=45)["result"]
        except (RuntimeError, urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[warn] {e}; повтор через 5 с")
            time.sleep(5)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            if text.startswith("/start") or text.startswith("/open"):
                try:
                    send_start(msg["chat"]["id"], (msg.get("from") or {}).get("first_name", ""))
                except RuntimeError as e:
                    print(f"[warn] {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Бот остановлен.")
