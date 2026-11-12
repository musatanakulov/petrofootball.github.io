"""Ежедневная копия базы данных.

- Копия делается встроенным механизмом SQLite (backup API): она целостна, даже если сервер в этот момент работает.
- Сервер делает копию при запуске (если последней больше 20 часов) и затем раз в сутки, пока работает.
- Хранятся последние BACKUP_KEEP копий (по умолчанию 30), старые удаляются.

Настройки (переменные окружения или .env):
  BACKUP_DIR        куда складывать копии (по умолчанию backups/daily рядом с кодом)
  BACKUP_COPY_DIR   необязательно: вторая папка (другой диск, облако, флешка) — копия кладётся и туда
  BACKUP_KEEP       сколько копий хранить (30)
  BACKUP_SCHEDULER  0 — не запускать фоновую ежедневную копию

Ручной запуск:   py backup.py
Восстановление:  остановить сервер и заменить football_tournament.db нужной копией из папки backups/daily.
"""
import glob
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime

from sqlalchemy.engine.url import make_url

import database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PREFIX = "football_tournament_"
MAX_AGE_HOURS = 20          # при запуске сервера делаем копию, если последней больше этого
CHECK_EVERY_SECONDS = 30 * 60


def backup_dir() -> str:
    return os.getenv("BACKUP_DIR") or os.path.join(BASE_DIR, "backups", "daily")


def database_file() -> str:
    path = make_url(database.DATABASE_URL).database
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(os.getcwd(), path))


def list_backups() -> list[str]:
    return sorted(glob.glob(os.path.join(backup_dir(), PREFIX + "*.db")))


def latest_backup_age_hours() -> float | None:
    files = list_backups()
    return (time.time() - os.path.getmtime(files[-1])) / 3600 if files else None


def backup_now() -> str:
    """Делает копию, проверяет её целостность, удаляет лишние старые. Возвращает путь к копии."""
    os.makedirs(backup_dir(), exist_ok=True)
    target = os.path.join(backup_dir(), f"{PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
    src = sqlite3.connect(database_file())
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        dst.close(); src.close()
    if not ok:
        os.remove(target)
        raise RuntimeError("копия базы не прошла проверку целостности")

    copy_dir = os.getenv("BACKUP_COPY_DIR")
    if copy_dir:
        try:
            os.makedirs(copy_dir, exist_ok=True)
            shutil.copy2(target, copy_dir)
        except OSError as e:  # вторая копия — «по возможности», основная уже сделана
            print(f"[backup] не удалось скопировать во вторую папку: {e}")

    keep = int(os.getenv("BACKUP_KEEP", "30"))
    for old in list_backups()[:-keep]:
        os.remove(old)
    return target


def ensure_recent_backup(max_age_hours: float = MAX_AGE_HOURS) -> str | None:
    """Делает копию, если свежей нет. Возвращает путь или None (если свежая уже есть)."""
    age = latest_backup_age_hours()
    if age is not None and age < max_age_hours:
        return None
    return backup_now()


def start_scheduler() -> None:
    """Фоновый поток: раз в полчаса проверяет, не пора ли сделать суточную копию."""
    if os.getenv("BACKUP_SCHEDULER", "1") == "0":
        return

    def loop():
        while True:
            time.sleep(CHECK_EVERY_SECONDS)
            try:
                path = ensure_recent_backup(24)
                if path:
                    print(f"[backup] суточная копия базы: {path}")
            except Exception as e:  # сбой копии не должен ронять сервер
                print(f"[backup] ОШИБКА: {e!r}")

    threading.Thread(target=loop, name="db-backup", daemon=True).start()


if __name__ == "__main__":
    print("Копия создана:", backup_now())
    print(f"Хранится копий: {len(list_backups())} в {backup_dir()}")
