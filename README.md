# Кубок Акима города Петропавловск 2026-2027 — Telegram Mini App

FastAPI + SQLite, фронтенд на чистом JS, бот на long polling.

## Запуск
    py -m pip install -r requirements.txt
    py -m uvicorn main:app --host 127.0.0.1 --port 8000
Для Telegram (сервер + туннель + бот): `.\start_telegram.ps1`

## Настройка (.env, в git не попадает)
- `BOT_TOKEN` — токен бота
- `ADMIN_TELEGRAM_IDS` — Telegram ID админов через запятую
- `ALLOW_DEV_ADMIN=1` — только для локальной разработки, на сервере не включать
- `BACKUP_COPY_DIR` — (необязательно) куда дополнительно копировать резервные копии базы

## Тесты
    py -m pip install -r requirements-dev.txt
    py -m pytest

## Резервные копии
Раз в сутки в `backups/daily` (хранится 30 копий).
