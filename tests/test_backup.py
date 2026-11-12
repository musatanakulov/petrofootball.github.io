"""Ежедневная копия базы: целостность, свежесть, ограничение количества, восстановление."""
import os
import shutil
import sqlite3
import time

import backup


def clear_backups():
    shutil.rmtree(backup.backup_dir(), ignore_errors=True)


def test_backup_is_a_consistent_copy_of_the_database(client, mini):
    clear_backups()
    path = backup.backup_now()
    assert os.path.exists(path) and path.startswith(backup.backup_dir())
    copy = sqlite3.connect(path)
    try:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 4              # данные мини-лиги на месте
        assert copy.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 12
    finally:
        copy.close()


def test_recent_backup_is_not_repeated_but_stale_one_is(client):
    clear_backups()
    first = backup.ensure_recent_backup()
    assert first is not None                                    # свежей копии не было -> сделали
    assert backup.ensure_recent_backup() is None                # свежая есть -> не дублируем
    old = time.time() - 30 * 3600
    os.utime(first, (old, old))                                 # «состарим» копию
    assert backup.latest_backup_age_hours() > 24
    assert backup.ensure_recent_backup() is not None            # устарела -> новая


def test_only_the_newest_copies_are_kept(client, monkeypatch):
    clear_backups()
    monkeypatch.setenv("BACKUP_KEEP", "3")
    os.makedirs(backup.backup_dir())
    for day in range(1, 6):                                     # пять старых копий
        stub = os.path.join(backup.backup_dir(), f"{backup.PREFIX}2020010{day}-000000.db")
        open(stub, "wb").close()
    backup.backup_now()
    files = backup.list_backups()
    assert len(files) == 3
    assert files[-1].split(backup.PREFIX)[1].startswith(str(time.localtime().tm_year))   # самая новая осталась


def test_second_copy_goes_to_extra_folder(client, monkeypatch, tmp_path):
    clear_backups()
    monkeypatch.setenv("BACKUP_COPY_DIR", str(tmp_path / "second"))
    path = backup.backup_now()
    assert (tmp_path / "second" / os.path.basename(path)).exists()


def test_restore_by_replacing_the_file(client, mini):
    """Копию можно открыть как обычную базу — так и выглядит восстановление."""
    clear_backups()
    path = backup.backup_now()
    copy = sqlite3.connect(path)
    names = {r[0] for r in copy.execute("SELECT name FROM teams")}
    copy.close()
    assert names == {"Команда A", "Команда B", "Команда C", "Команда D"}
