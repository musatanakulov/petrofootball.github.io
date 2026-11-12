"""Загрузки: сжатие картинок, лимит заявки 25 игроков, проверка файлов."""
import io
import random

import docx
import pytest
from PIL import Image, ImageDraw

import images
import main
from conftest import ADMIN


@pytest.fixture(autouse=True)
def media_dirs(tmp_path, monkeypatch):
    """Картинки пишем во временные папки, а не в static/ проекта."""
    logos, players = tmp_path / "logos", tmp_path / "players"
    logos.mkdir(); players.mkdir()
    monkeypatch.setattr(main, "LOGO_DIR", str(logos))
    monkeypatch.setattr(main, "PLAYER_DIR", str(players))
    return {"logos": logos, "players": players}


def noise(w, h, fmt, **kw):
    random.seed(1)
    b = io.BytesIO(); Image.effect_noise((w, h), 80).convert("RGB").save(b, fmt, **kw)
    return b.getvalue()


def upload(client, files):
    return client.post("/api/upload-media-bulk", headers=ADMIN(), files=[("files", (n, d, "application/octet-stream")) for n, d in files])


def test_optimizer_resizes_keeps_alpha_and_applies_exif_rotation():
    logo = Image.new("RGBA", (2500, 2500), (0, 0, 0, 0)); ImageDraw.Draw(logo).ellipse((200, 200, 2300, 2300), fill=(30, 90, 220, 255))
    b = io.BytesIO(); logo.save(b, "PNG")
    out = Image.open(io.BytesIO(images.optimize_image(b.getvalue(), images.LOGO_MAX_SIDE))).convert("RGBA")
    assert out.size == (256, 256) and out.getpixel((2, 2))[3] == 0 and out.getpixel((128, 128))[3] == 255
    tall = Image.new("RGB", (300, 100), (200, 10, 10)); exif = tall.getexif(); exif[274] = 6
    b = io.BytesIO(); tall.save(b, "JPEG", exif=exif)
    assert Image.open(io.BytesIO(images.optimize_image(b.getvalue(), 400))).size == (100, 300)
    small = io.BytesIO(); Image.new("RGB", (120, 80)).save(small, "PNG")
    assert Image.open(io.BytesIO(images.optimize_image(small.getvalue(), 400))).size == (120, 80)     # маленькие не раздуваются
    with pytest.raises(images.NotAnImage):
        images.optimize_image(b"not an image", 400)


def test_bulk_upload_compresses_and_links_webp(client, mini, media_dirs):
    big_photo = noise(3000, 2000, "JPEG", quality=95)
    r = upload(client, [("ИГРОК a1.JPG", big_photo), ("команда a.png", noise(2000, 2000, "PNG")), ("unknown.png", big_photo), ("notes.txt", b"hello")])
    assert r.status_code == 200
    body = r.json()
    assert "Фото игроков: 1" in body["message"] and "логотипов команд: 1" in body["message"] and "сжаты" in body["message"]
    assert set(body["unmatched_files"]) == {"unknown.png", "notes.txt"}
    leagues = client.get("/api/leagues").json()
    player = next(p for l in leagues for t in l["teams"] for p in t["players"] if p["name"] == "Игрок A1")
    team = next(t for l in leagues for t in l["teams"] if t["name"] == "Команда A")
    assert player["photo_url"].endswith(".webp") and team["logo_url"].endswith(".webp")
    photo_file = media_dirs["players"] / player["photo_url"].rsplit("/", 1)[1]
    assert photo_file.stat().st_size < len(big_photo) / 20 and max(Image.open(photo_file).size) <= images.PHOTO_MAX_SIDE


def test_bulk_upload_rejects_non_image_with_known_name(client, mini):
    r = upload(client, [("Игрок A1.png", b"not an image")])
    assert any("не удалось прочитать" in u for u in r.json()["unmatched_files"])


def test_bulk_upload_requires_admin(client, mini):
    r = client.post("/api/upload-media-bulk", files=[("files", ("x.png", b"1", "application/octet-stream"))])
    assert r.status_code == 403


def roster_docx(n, prefix="Тестов"):
    d = docx.Document(); t = d.add_table(rows=n + 1, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "№", "Ф И О"
    for i in range(1, n + 1):
        t.cell(i, 0).text, t.cell(i, 1).text = str(i), f"{prefix} Игрок{i:02d}"
    b = io.BytesIO(); d.save(b); return b.getvalue()


def upload_roster(client, team, n, logo=None, league="Третья лига"):
    files = [("file", ("z.docx", roster_docx(n), "application/octet-stream"))]
    if logo is not None:
        files.append(("logo", ("logo.png", logo, "application/octet-stream")))
    return client.post("/api/upload-roster", headers=ADMIN(), data={"league_name": league, "team_name": team}, files=files)


def players_of(client, name):
    return sum(len(t["players"]) for l in client.get("/api/leagues").json() for t in l["teams"] if t["name"] == name)


def test_roster_limit_is_25_players(client):
    r = upload_roster(client, "Лимит 26", 26)
    assert r.status_code == 400 and "4.3" in r.json()["detail"] and players_of(client, "Лимит 26") == 0   # ничего не сохранилось
    assert upload_roster(client, "Лимит 25", 25).status_code == 200 and players_of(client, "Лимит 25") == 25
    assert upload_roster(client, "Лимит 25", 26).status_code == 400                                       # 26-й к полной команде не добавить
    assert upload_roster(client, "Лимит 25", 25).json()["message"].count("добавлено игроков 0")           # повторная загрузка без дублей


def test_roster_logo_is_compressed_and_validated(client, media_dirs):
    r = upload_roster(client, "Логотип-тест", 1, logo=noise(2500, 2500, "PNG"))
    assert r.status_code == 200
    team = next(t for l in client.get("/api/leagues").json() for t in l["teams"] if t["name"] == "Логотип-тест")
    assert team["logo_url"].endswith(".webp")
    assert upload_roster(client, "Логотип-тест", 1, logo=b"not an image").status_code == 400


def test_rename_team_keeps_old_name_as_alias(client, mini, media_dirs):
    """«NOMAD» -> «NOMAD TEAM»: старые файлы и графики со старым названием не создают дубль."""
    team = mini["teams"]["A"]
    assert client.post(f"/api/teams/{team}/rename", json={"name": "Новое имя"}).status_code == 403         # только админ
    assert client.post(f"/api/teams/{team}/rename", headers=ADMIN(), json={"name": "   "}).status_code in (400, 422)
    assert client.post(f"/api/teams/{team}/rename", headers=ADMIN(), json={"name": "команда b"}).status_code == 409   # такая уже есть
    r = client.post(f"/api/teams/{team}/rename", headers=ADMIN(), json={"name": "Команда  A  TEAM"})
    assert r.status_code == 200 and r.json()["aliases"] == ["Команда A"]
    teams = {t["id"]: t["name"] for lg in client.get("/api/leagues").json() for t in lg["teams"]}
    assert teams[team] == "Команда A TEAM"
    # файл логотипа со СТАРЫМ названием находит ту же команду
    r = upload(client, [("КОМАНДА a.png", noise(400, 400, "PNG"))])
    assert "логотипов команд: 1" in r.json()["message"]
    renamed = next(t for lg in client.get("/api/leagues").json() for t in lg["teams"] if t["id"] == team)
    assert renamed["logo_url"].endswith(".webp")
    # загрузка состава по старому названию не создаёт вторую команду
    count = lambda: sum(len(lg["teams"]) for lg in client.get("/api/leagues").json())   # noqa: E731
    before = count()
    assert upload_roster(client, "Команда A", 3, league="Первая лига").status_code == 200
    assert count() == before
    # возврат к старому названию убирает его из псевдонимов
    r = client.post(f"/api/teams/{team}/rename", headers=ADMIN(), json={"name": "Команда A"})
    assert r.json()["aliases"] == ["Команда A TEAM"]


def test_player_names_are_surname_and_name_only(client, mini, media_dirs):
    """Отчество не храним: в составе — «Фамилия Имя»; файл фото с отчеством находит того же игрока."""
    d = docx.Document(); t = d.add_table(rows=4, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "№", "Ф И О"
    for i, name in enumerate(["Сидоров Пётр Иванович", "Madaminov Jakhongir Makhmudjon ugli", "Намазов Эминали"], 1):
        t.cell(i, 0).text, t.cell(i, 1).text = str(i), name
    b = io.BytesIO(); d.save(b)
    r = client.post("/api/upload-roster", headers=ADMIN(), data={"league_name": "Третья лига", "team_name": "Отчества"},
                    files=[("file", ("z.docx", b.getvalue(), "application/octet-stream"))])
    assert r.status_code == 200 and players_of(client, "Отчества") == 3
    names = {p["name"] for l in client.get("/api/leagues").json() for tm in l["teams"] if tm["name"] == "Отчества" for p in tm["players"]}
    assert names == {"Сидоров Пётр", "Madaminov Jakhongir", "Намазов Эминали"}
    # повторная загрузка того же файла (или с отчеством) не создаёт дублей
    client.post("/api/upload-roster", headers=ADMIN(), data={"league_name": "Третья лига", "team_name": "Отчества"},
                files=[("file", ("z.docx", b.getvalue(), "application/octet-stream"))])
    assert players_of(client, "Отчества") == 3
    # фото с отчеством в имени файла
    r = upload(client, [("сидоров пётр иванович.jpg", noise(600, 600, "JPEG"))])
    assert "Фото игроков: 1" in r.json()["message"]
