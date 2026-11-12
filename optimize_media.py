"""Однократно сжимает уже загруженные картинки (логотипы, фото игроков, эмблемы в шапке).

  py optimize_media.py --dry-run   # только показать, что и насколько сожмётся
  py optimize_media.py             # сжать; оригиналы переносятся в backups/media-originals/

Каждая картинка перекодируется в WebP нужного размера, ссылки в базе (teams.logo_url, players.photo_url)
обновляются. Безопасно запускать повторно: уже сжатые файлы пропускаются.
"""
import os
import shutil
import sqlite3
import sys

from images import LOGO_MAX_SIDE, PHOTO_MAX_SIDE, NotAnImage, optimize_image

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "football_tournament.db")
BACKUP_DIR = os.path.join(BASE, "backups", "media-originals")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
SMALL_ENOUGH = 120 * 1024  # webp меньше этого размера считаем уже сжатым

dry = "--dry-run" in sys.argv


def mb(n):
    return f"{n / 1024 / 1024:.2f} МБ"


def convert(path, max_side):
    """Возвращает (новый_путь, размер_до, размер_после) или None, если файл пропущен."""
    stem, ext = os.path.splitext(path)
    ext = ext.lower()
    if ext not in IMAGE_EXT:
        return None
    before = os.path.getsize(path)
    if ext == ".webp" and before <= SMALL_ENOUGH:
        return None  # уже сжат
    with open(path, "rb") as f:
        data = f.read()
    try:
        out = optimize_image(data, max_side)
    except NotAnImage:
        print(f"  ! пропущен (не картинка): {os.path.relpath(path, BASE)}")
        return None
    return stem + ".webp", before, out


def main():
    conn = sqlite3.connect(DB_PATH)
    jobs = []  # (папка в URL, путь, лимит стороны)
    for sub, max_side in (("logos", LOGO_MAX_SIDE), ("players", PHOTO_MAX_SIDE)):
        d = os.path.join(BASE, "static", sub)
        if os.path.isdir(d):
            jobs += [(sub, os.path.join(d, n), max_side) for n in sorted(os.listdir(d))]
    jobs += [("", os.path.join(BASE, n), LOGO_MAX_SIDE) for n in ("logo1.png", "logo2.png") if os.path.exists(os.path.join(BASE, n))]

    total_before = total_after = 0
    converted = []  # (sub, старое имя, новое имя)
    for sub, path, max_side in jobs:
        res = convert(path, max_side)
        if not res:
            continue
        new_path, before, webp = res
        total_before += before
        total_after += len(webp)
        print(f"  {os.path.relpath(path, BASE):<62} {before / 1024:>8.0f} КБ -> {len(webp) / 1024:>5.0f} КБ")
        converted.append((sub, os.path.basename(path), os.path.basename(new_path)))
        if dry:
            continue
        rel = os.path.relpath(path, BASE)
        backup = os.path.join(BACKUP_DIR, rel)
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        if os.path.abspath(new_path) == os.path.abspath(path):  # webp -> webp: оригинал в бэкап, новый на его место
            shutil.copy2(path, backup)
        else:
            shutil.move(path, backup)
        with open(new_path, "wb") as f:
            f.write(webp)

    if not dry and converted:
        with conn:
            for sub, old, new in converted:
                if sub == "logos":
                    conn.execute("UPDATE teams SET logo_url = ? WHERE logo_url = ?", (f"/static/logos/{new}", f"/static/logos/{old}"))
                elif sub == "players":
                    conn.execute("UPDATE players SET photo_url = ? WHERE photo_url = ?", (f"/static/players/{new}", f"/static/players/{old}"))
        # Дубликаты, на которые не ссылается ни одна запись (напр. «SPARTA STS PROM» и «SPARTA_STS_PROM»), удаляем
        used = {u for (u,) in conn.execute("SELECT logo_url FROM teams WHERE logo_url IS NOT NULL")} | \
               {u for (u,) in conn.execute("SELECT photo_url FROM players WHERE photo_url IS NOT NULL")}
        removed = 0
        for sub, _old, new in converted:
            if sub in ("logos", "players") and f"/static/{sub}/{new}" not in used:
                p = os.path.join(BASE, "static", sub, new)
                if os.path.exists(p):
                    os.remove(p)
                    removed += 1
        if removed:
            print(f"Удалено неиспользуемых дубликатов: {removed} (оригиналы сохранены в backups/media-originals/)")
    conn.close()

    print()
    if not converted:
        print("Нечего сжимать: всё уже оптимизировано.")
    else:
        print(f"{'Было бы сжато' if dry else 'Сжато'} файлов: {len(converted)}. {mb(total_before)} -> {mb(total_after)}"
              f" (в {total_before / max(total_after, 1):.0f} раз меньше).")
        if not dry:
            print("Оригиналы: backups/media-originals/")


if __name__ == "__main__":
    main()
