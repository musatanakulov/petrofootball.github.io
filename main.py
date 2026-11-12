from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from database import SessionLocal, init_db, mark_backfill_done, League, Team, Player, Match, MatchEvent, PlayerVote, Sanction, RoundClosure, Subscriber
import backup
from images import optimize_image, MAX_UPLOAD_BYTES, LOGO_MAX_SIDE, PHOTO_MAX_SIDE
from config import BOT_TOKEN, ADMIN_TELEGRAM_IDS, ALLOW_DEV_ADMIN, INIT_DATA_MAX_AGE, TZ_OFFSET_HOURS
from datetime import date, datetime, timedelta, timezone
from pydantic import BaseModel, Field
from typing import List, Optional
from urllib.parse import parse_qsl
from itertools import groupby
import unicodedata
import hashlib
import hmac
import json
import time
import re
import docx
import pandas as pd
import io
import os
import shutil

app = FastAPI(title="Кубок Акима города Петропавловск API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_DIR = os.path.join(BASE_DIR, "static", "logos")
PLAYER_DIR = os.path.join(BASE_DIR, "static", "players")
os.makedirs(LOGO_DIR, exist_ok=True)
os.makedirs(PLAYER_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Старый формат ссылок на медиа (до перехода на относительные пути)
LEGACY_MEDIA_PREFIX = "http://127.0.0.1:8000"

# Регламент: в заявочном листе не более 25 игроков (ст. 4.3); матч 2×20 минут, доп. время 2×10 (ст. 3.12, 8.2)
MAX_ROSTER = 25
MAX_MINUTE = 60

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Нормализация строк для сравнения: Unicode NFC, без учёта регистра (casefold корректно
# работает с кириллицей и казахскими Ө Қ Ғ Ә Ү Ұ І Ң Һ), "_" = пробел, лишние пробелы схлопываются.
def norm(text: Optional[str]) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).replace("_", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()

def normalize_round_name(raw: str) -> str:
    """Единый вид названия тура: «Первый тур», «1 тур», «тур 1» -> «Тур 1» (чтобы не плодить дубли при повторной загрузке графика)."""
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    low = norm(name)
    m = re.fullmatch(r"(?:тур\s*(\d+)|(\d+)\s*-?\s*(?:й\s*)?тур)", low)
    if m:
        return f"Тур {int(m.group(1) or m.group(2))}"
    m = re.fullmatch(r"([а-яё]+)\s+тур", low)
    if m and m.group(1) in ROUND_ORDINALS:
        return f"Тур {ROUND_ORDINALS[m.group(1)]}"
    return name

def save_optimized_image(upload: UploadFile, folder: str, url_prefix: str, name_stem: str, max_side: int):
    """Читает загруженную картинку, сжимает (WebP, не больше max_side px) и сохраняет как <имя>.webp.
    Возвращает (url, байт_до, байт_после). ValueError — файл слишком большой или это не картинка."""
    data = upload.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"файл больше {MAX_UPLOAD_BYTES // 1024 // 1024} МБ")
    webp = optimize_image(data, max_side)  # NotAnImage — подкласс ValueError
    fname = safe_filename(name_stem) + ".webp"
    with open(os.path.join(folder, fname), "wb") as f:
        f.write(webp)
    return f"{url_prefix}/{fname}", len(data), len(webp)

def safe_filename(filename: str) -> str:
    """Только имя файла (без путей), пробелы заменены на '_'."""
    name = os.path.basename((filename or "").replace("\\", "/"))
    name = unicodedata.normalize("NFC", name).replace(" ", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name)
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Некорректное имя файла")
    return name

# --- Проверка администратора через подпись Telegram WebApp initData ---
def verify_telegram_init_data(init_data: Optional[str]) -> Optional[dict]:
    """Возвращает данные пользователя Telegram, если подпись initData верна, иначе None.
    Алгоритм: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    if not init_data or not BOT_TOKEN:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "{}"))
    except (ValueError, json.JSONDecodeError):
        return None
    if time.time() - auth_date > INIT_DATA_MAX_AGE:
        return None
    return user if isinstance(user, dict) else None

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
# Заголовки, которые добавляют прокси и туннели (ngrok, Cloudflare, nginx).
# Если они есть — запрос пришёл извне, даже если TCP-соединение локальное.
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip", "x-forwarded-host")

def is_local_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host not in LOOPBACK_HOSTS:
        return False
    return not any(h in request.headers for h in PROXY_HEADERS)

def is_admin_request(request: Request, init_data: Optional[str]) -> bool:
    # Режим разработки: админ без Telegram, но ТОЛЬКО с этого же компьютера
    if ALLOW_DEV_ADMIN and is_local_request(request):
        return True
    user = verify_telegram_init_data(init_data)
    return bool(user) and user.get("id") in ADMIN_TELEGRAM_IDS

def require_admin(request: Request, x_telegram_init_data: Optional[str] = Header(None)):
    if not is_admin_request(request, x_telegram_init_data):
        raise HTTPException(status_code=403, detail="Доступ только для администратора")

@app.get("/api/auth/check")
def auth_check(request: Request, x_telegram_init_data: Optional[str] = Header(None)):
    return {"is_admin": is_admin_request(request, x_telegram_init_data)}

@app.on_event("startup")
def startup_event():
    needs_backfill = init_db()
    db = SessionLocal()
    if needs_backfill:
        try:
            backfill_cards_and_sanctions(db)  # один раз при обновлении со старой версии данных
            mark_backfill_done(db)
            db.commit()
            print("[migration] карточки и санкции пересчитаны")
        except Exception as e:  # не мешаем запуску сервера; повторим при следующем старте
            db.rollback()
            print(f"[migration] ОШИБКА пересчёта карточек/санкций: {e!r}")
    leagues_data = ["Первая лига", "Вторая лига", "Третья лига"]

    existing_leagues = db.query(League).all()
    existing_names = [norm(l.name) for l in existing_leagues]

    for l_name in leagues_data:
        if norm(l_name) not in existing_names:
            db.add(League(name=l_name))
            existing_names.append(norm(l_name))

    # Миграция: абсолютные ссылки http://127.0.0.1:8000/static/... -> относительные /static/...
    for team in db.query(Team).filter(Team.logo_url.like(f"{LEGACY_MEDIA_PREFIX}/%")).all():
        team.logo_url = team.logo_url[len(LEGACY_MEDIA_PREFIX):]
    for player in db.query(Player).filter(Player.photo_url.like(f"{LEGACY_MEDIA_PREFIX}/%")).all():
        player.photo_url = player.photo_url[len(LEGACY_MEDIA_PREFIX):]

    db.commit()
    db.close()

    # Ежедневная копия базы: при запуске (если свежей нет) и затем раз в сутки, пока сервер работает
    try:
        path = backup.ensure_recent_backup()
        if path:
            print(f"[backup] копия базы: {path}")
    except Exception as e:  # сбой копии не должен мешать запуску
        print(f"[backup] ОШИБКА: {e!r}")
    backup.start_scheduler()

# --- Раздача фронтенда с того же адреса, что и API (только явные файлы, не вся папка) ---
FRONTEND_FILES = {"index.html", "app.js", "style.css", "logo1.webp", "logo2.webp", "design-system.html", "i18n.js"}

# no-cache: браузер каждый раз сверяется с сервером (ETag), поэтому после правок стиля/скриптов
# не остаётся «старой» версии из кэша
NO_CACHE = {"Cache-Control": "no-cache"}

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"), headers=NO_CACHE)

@app.get("/{filename}", include_in_schema=False)
def serve_frontend_file(filename: str):
    if filename not in FRONTEND_FILES:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(os.path.join(BASE_DIR, filename), headers=NO_CACHE)

class MatchEventCreate(BaseModel):
    player_id: int
    team_id: int
    event_type: str  # goal, own_goal, yellow_card, red_card
    minute: Optional[int] = Field(None, ge=0, le=MAX_MINUTE)  # матч 2×20 минут (ст. 3.12); с доп. временем — до 60

class MatchResultUpdate(BaseModel):
    home_score: int = Field(..., ge=0, le=99)   # счёт не бывает отрицательным
    away_score: int = Field(..., ge=0, le=99)
    technical: bool = False                      # технический результат 5:0 / 0:5 (ст. 3.9–3.11, 9.4)
    man_of_the_match_id: Optional[int] = None
    events: List[MatchEventCreate] = []

@app.get("/api/leagues")
def get_leagues(db: Session = Depends(get_db)):
    leagues = db.query(League).all()
    result = []
    for league in leagues:
        teams_list = [{
            "id": t.id, 
            "name": t.name, 
            "logo_url": t.logo_url,
            "players": [{
                "id": p.id, "name": p.name, "number": p.number,
                "photo_url": p.photo_url,
                "stats": {
                    "goals": p.goals, "own_goals": p.own_goals,
                    "yellow_cards": p.yellow_cards, "red_cards": p.red_cards,
                    "man_of_the_match": p.man_of_the_match_count
                }
            } for p in t.players]
        } for t in league.teams]
        result.append({"id": league.id, "name": league.name, "teams": teams_list})
    return result

@app.post("/api/upload-media-bulk", dependencies=[Depends(require_admin)])
def upload_media_bulk(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db)
):
    teams_updated = 0
    players_updated = 0
    unmatched = []
    bytes_in = bytes_out = 0

    all_teams = db.query(Team).all()
    all_players = db.query(Player).all()

    for file in files:
        base_name, ext = os.path.splitext(file.filename)
        clean_name = norm(base_name)

        matched_team = None
        for team in all_teams:
            if norm(team.name) == clean_name:
                matched_team = team
                break

        if matched_team:
            try:
                url, n_in, n_out = save_optimized_image(file, LOGO_DIR, "/static/logos", base_name, LOGO_MAX_SIDE)
            except ValueError as e:
                unmatched.append(f"{file.filename} ({e})")
                continue
            matched_team.logo_url = url
            bytes_in += n_in; bytes_out += n_out
            teams_updated += 1
            continue
        
        matched_player = None
        for player in all_players:
            if norm(player.name) == clean_name:
                matched_player = player
                break

        if matched_player:
            try:
                url, n_in, n_out = save_optimized_image(file, PLAYER_DIR, "/static/players", base_name, PHOTO_MAX_SIDE)
            except ValueError as e:
                unmatched.append(f"{file.filename} ({e})")
                continue
            matched_player.photo_url = url
            bytes_in += n_in; bytes_out += n_out
            players_updated += 1
            continue
        
        unmatched.append(file.filename)
    
    db.commit()
    return {
        "status": "success",
        "message": (f"Загружено логотипов команд: {teams_updated}. Фото игроков: {players_updated}."
                    + (f" Картинки сжаты: {bytes_in / 1024 / 1024:.1f} МБ → {bytes_out / 1024 / 1024:.2f} МБ." if bytes_in else "")),
        "unmatched_files": unmatched
    }

@app.post("/api/upload-schedule-excel", dependencies=[Depends(require_admin)])
def upload_schedule_excel(
    league_name: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    clean_league_name = league_name.strip()
    leagues = db.query(League).all()
    matched_league = next((l for l in leagues if norm(l.name) == norm(clean_league_name)), None)

    if not matched_league:
        matched_league = League(name=clean_league_name)
        db.add(matched_league)
        db.commit()
        db.refresh(matched_league)

    try:
        contents = file.file.read()
        df = pd.read_excel(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения Excel файла: {str(e)}")

    added = 0
    all_teams = db.query(Team).filter(Team.league_id == matched_league.id).all()

    for index, row in df.iterrows():
        round_name = normalize_round_name(str(row.iloc[0]))
        for col in df.columns[1:]:
            val = row[col]
            if pd.notna(val):
                parts = str(val).split(' — ')
                if len(parts) == 2:
                    home_name = parts[0].strip()
                    away_name = parts[1].strip()

                    home_team = next((t for t in all_teams if norm(t.name) == norm(home_name)), None)
                    if not home_team:
                        home_team = Team(name=home_name, logo_url="https://via.placeholder.com/80", league_id=matched_league.id)
                        db.add(home_team)
                        db.commit()
                        db.refresh(home_team)
                        all_teams.append(home_team)

                    away_team = next((t for t in all_teams if norm(t.name) == norm(away_name)), None)
                    if not away_team:
                        away_team = Team(name=away_name, logo_url="https://via.placeholder.com/80", league_id=matched_league.id)
                        db.add(away_team)
                        db.commit()
                        db.refresh(away_team)
                        all_teams.append(away_team)

                    existing_match = db.query(Match).filter(
                        Match.league_id == matched_league.id,
                        Match.round_name == round_name,
                        Match.home_team_id == home_team.id,
                        Match.away_team_id == away_team.id
                    ).first()

                    if not existing_match:
                        db.add(Match(
                            league_id=matched_league.id,
                            round_name=round_name,
                            match_date="",
                            match_time="",
                            home_team_id=home_team.id,
                            away_team_id=away_team.id,
                            status="scheduled"
                        ))
                        added += 1

    db.commit()
    return {"status": "success", "message": f"Загружено матчей из Excel: {added}"}

@app.post("/api/upload-roster", dependencies=[Depends(require_admin)])
def upload_team_roster(
    league_name: str = Form(...), team_name: str = Form(...),
    file: UploadFile = File(...), logo: UploadFile = File(None), db: Session = Depends(get_db)
):
    leagues = db.query(League).all()
    matched_league = next((l for l in leagues if norm(l.name) == norm(league_name)), None)
    if not matched_league: raise HTTPException(status_code=400, detail="Неверное название лиги")

    logo_url = None
    if logo and logo.filename:
        try:
            logo_url, _, _ = save_optimized_image(logo, LOGO_DIR, "/static/logos", team_name, LOGO_MAX_SIDE)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Логотип: {e}")

    teams = db.query(Team).filter(Team.league_id == matched_league.id).all()
    matched_team = next((t for t in teams if norm(t.name) == norm(team_name)), None)

    if not matched_team:
        matched_team = Team(name=team_name.strip(), league_id=matched_league.id)
        if logo_url: matched_team.logo_url = logo_url
        db.add(matched_team); db.commit(); db.refresh(matched_team)
    elif logo_url:
        # Логотип меняем только если его действительно прислали
        matched_team.logo_url = logo_url; db.commit()

    try:
        contents = file.file.read()
        doc = docx.Document(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения файла: {str(e)}")

    all_players = db.query(Player).filter(Player.team_id == matched_team.id).all()
    added = 0

    for table in doc.tables:
        for row in table.rows[1:]:
            cells = [cell.text.strip() for cell in row.cells]
            if len(cells) >= 2:
                row_num, p_name = cells[0], cells[1]
                if row_num.isdigit() and p_name and p_name != "Ф И О":
                    existing = next((p for p in all_players if norm(p.name) == norm(p_name)), None)
                    if not existing:
                        new_p = Player(name=p_name.strip(), number=int(row_num), position="Игрок", team_id=matched_team.id)
                        db.add(new_p)
                        all_players.append(new_p)
                        added += 1
    if len(all_players) > MAX_ROSTER:
        db.rollback()  # новых игроков не сохраняем
        raise HTTPException(status_code=400, detail=(
            f"В заявочном листе команды не может быть больше {MAX_ROSTER} игроков (регламент, ст. 4.3): "
            f"в файле и в базе набирается {len(all_players)}. Уберите лишних из файла и загрузите снова."))
    db.commit()
    return {"status": "success",
            "message": f"Команда '{matched_team.name}': добавлено игроков {added}, всего в составе {len(all_players)}."}

# Расписание: только предстоящие матчи (scheduled)
@app.get("/api/matches/upcoming")
def get_upcoming_matches(db: Session = Depends(get_db)):
    matches = db.query(Match).filter(Match.status == "scheduled", Match.annulled == False).all()  # noqa: E712
    result = []
    for m in matches:
        result.append({
            "id": m.id,
            "league_id": m.league_id,
            "league": m.league.name,
            "round": m.round_name,
            **match_when(m),
            "home_team_id": m.home_team_id,
            "home_team": m.home_team.name,
            "home_logo": m.home_team.logo_url,
            "away_team_id": m.away_team_id,
            "away_team": m.away_team.name,
            "away_logo": m.away_team.logo_url,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "status": m.status
        })
    return result

# ---------------------------------------------------------------------------
# Карточки, дисквалификации и срок их действия
# Основано на регламенте, ст. 11: 11.2 две жёлтые в матче = удаление + 1 матч; 11.3 три жёлтые в разных матчах = 1 матч;
# 11.4 красная карточка = 1 матч; 11.5 Оргкомитет вправе изменить срок (админ правит санкцию вручную).
# - Две жёлтые одному игроку в матче = красная (удаление) + автоматическая дисквалификация на 1 матч.
# - Три жёлтые одному игроку в одном матче невозможны (ошибка при сохранении результата).
# - Срок дисквалификации в матчах считается по сыгранным матчам команды ПОСЛЕ матча с карточкой.
#   Когда срок вышел, игрок исчезает из списка «Дисквалификации».
# События матча хранятся «как ввёл админ» (жёлтая, жёлтая); красную из второй жёлтой считаем при подсчётах.
# ---------------------------------------------------------------------------
SECOND_YELLOW_REASON = "За второе предупреждение в матче"
STRAIGHT_RED_REASON = "Прямая красная карточка"
YELLOWS_REASON = "За накопление трёх предупреждений"
YELLOWS_PER_BAN = 3
UNPARSED_ROUND = 10**6
ROUND_ORDINALS = {"первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "четвёртый": 4, "пятый": 5,
                  "шестой": 6, "седьмой": 7, "восьмой": 8, "девятый": 9, "десятый": 10}

def effective_events(events):
    """[(событие, итоговый_тип)] в порядке ввода. Вторая жёлтая игрока в матче -> 'second_yellow' (красная)."""
    yellows, result = {}, []
    for ev in events:
        etype = ev.event_type
        if etype == "yellow_card":
            yellows[ev.player_id] = yellows.get(ev.player_id, 0) + 1
            if yellows[ev.player_id] == 2:
                etype = "second_yellow"
        result.append((ev, etype))
    return result

def validate_match_events(db: Session, match: Match, events) -> None:
    team_ids = {match.home_team_id, match.away_team_id}
    yellow, red, names = {}, {}, {}
    for ev in events:
        if ev.event_type not in ("goal", "own_goal", "yellow_card", "red_card"):
            raise HTTPException(status_code=400, detail=f"Неизвестный тип события: {ev.event_type}")
        player = db.query(Player).filter(Player.id == ev.player_id).first()
        if not player:
            raise HTTPException(status_code=400, detail="Игрок из события не найден")
        if player.team_id not in team_ids:
            raise HTTPException(status_code=400, detail=f"{player.name} не играет в этом матче")
        names[player.id] = player.name
        if ev.event_type == "yellow_card": yellow[player.id] = yellow.get(player.id, 0) + 1
        if ev.event_type == "red_card": red[player.id] = red.get(player.id, 0) + 1
    for pid, name in names.items():
        if yellow.get(pid, 0) > 2:
            raise HTTPException(status_code=400, detail=f"{name}: три жёлтые карточки в одном матче невозможны. Вторая жёлтая уже означает красную.")
        if red.get(pid, 0) > 1:
            raise HTTPException(status_code=400, detail=f"{name}: нельзя удалить дважды в одном матче")
        if yellow.get(pid, 0) >= 2 and red.get(pid, 0) >= 1:
            raise HTTPException(status_code=400, detail=f"{name} уже удалён по второй жёлтой, отдельную красную добавлять не нужно")

def recompute_player_stats(db: Session, player_ids) -> None:
    """Пересчитывает голы/карточки/«Игрок матча» игроков по всем событиям (устойчиво к правкам результатов)."""
    ids = {pid for pid in player_ids if pid}
    if not ids:
        return
    stats = {pid: {"goals": 0, "own": 0, "yellow": 0, "red": 0, "motm": 0} for pid in ids}
    yellow_in_match = {}
    for ev in db.query(MatchEvent).join(Match, MatchEvent.match_id == Match.id).filter(MatchEvent.player_id.in_(ids), Match.annulled == False).order_by(MatchEvent.match_id, MatchEvent.id):  # noqa: E712
        s = stats[ev.player_id]
        if ev.event_type == "goal": s["goals"] += 1
        elif ev.event_type == "own_goal": s["own"] += 1
        elif ev.event_type == "red_card": s["red"] += 1
        elif ev.event_type == "yellow_card":
            key = (ev.match_id, ev.player_id)
            yellow_in_match[key] = yellow_in_match.get(key, 0) + 1
    # Две жёлтые в одном матче превращаются в красную: обе жёлтые «сгорают» (0 жёлтых), красная +1
    for (_, pid), n in yellow_in_match.items():
        if n >= 2: stats[pid]["red"] += 1
        else: stats[pid]["yellow"] += 1
    for pid, cnt in db.query(Match.man_of_the_match_id, func.count(Match.id)).filter(Match.man_of_the_match_id.in_(ids), Match.annulled == False).group_by(Match.man_of_the_match_id):  # noqa: E712
        stats[pid]["motm"] = cnt
    for p in db.query(Player).filter(Player.id.in_(ids)):
        s = stats[p.id]
        p.goals, p.own_goals, p.yellow_cards, p.red_cards, p.man_of_the_match_count = s["goals"], s["own"], s["yellow"], s["red"], s["motm"]

def round_no(name: Optional[str]) -> int:
    m = re.search(r"\d+", name or "")
    if m:
        return int(m.group())
    return ROUND_ORDINALS.get(norm(name).split(" ")[0] if name else "", UNPARSED_ROUND)

def is_after(m: Match, src: Match) -> bool:
    """Матч m в графике позже матча src (тур с тем же номером — дубль графика — не считается)."""
    a, b = round_no(m.round_name), round_no(src.round_name)
    if a != b:
        return a > b
    return a == UNPARSED_ROUND and m.id > src.id

def team_finished_matches(db: Session, team_id: int):
    return db.query(Match).filter(Match.status == "finished", Match.annulled == False, (Match.home_team_id == team_id) | (Match.away_team_id == team_id)).all()  # noqa: E712

def latest_finished_match_id(db: Session, team_id: Optional[int]) -> Optional[int]:
    ms = team_finished_matches(db, team_id) if team_id else []
    return max(ms, key=lambda m: (round_no(m.round_name), m.id)).id if ms else None

def matches_word(n: int) -> str:
    if 11 <= n % 100 <= 14: return "матчей"
    return {1: "матч", 2: "матча", 3: "матча", 4: "матча"}.get(n % 10, "матчей")

def sanction_state(db: Session, s: Sanction, team_id: Optional[int], today):
    """(действует_ли, текст срока, сколько матчей пропущено)."""
    if s.matches:
        finished = team_finished_matches(db, team_id) if team_id else []
        if s.source_match_id is None:
            served = len(finished)
        else:
            src = db.query(Match).filter(Match.id == s.source_match_id).first()
            served = sum(1 for m in finished if is_after(m, src)) if src else 0
        return served < s.matches, f"{s.matches} {matches_word(s.matches)} (пропущено {served} из {s.matches})", served
    if s.until_date:
        return today <= s.until_date, f"до {s.until_date.strftime('%d.%m.%Y')}", None
    return True, (s.term or ""), None

def sync_auto_sanctions(db: Session, match: Match, skip_players=()) -> None:
    """Создаёт/убирает автосанкции матча по его событиям. Уже существующие (в т.ч. отредактированные админом) не трогает."""
    events = db.query(MatchEvent).filter(MatchEvent.match_id == match.id).order_by(MatchEvent.id).all()
    desired = {}
    for ev, etype in effective_events(events):
        if ev.player_id in skip_players: continue
        if etype == "second_yellow": desired[(ev.player_id, "second_yellow")] = (SECOND_YELLOW_REASON, 1)
        elif etype == "red_card": desired[(ev.player_id, "red")] = (STRAIGHT_RED_REASON, 1)  # регламент 11.4: красная = 1 матч
    existing = {(s.player_id, s.auto): s for s in db.query(Sanction).filter(Sanction.source_match_id == match.id, Sanction.auto.in_(["second_yellow", "red"]))}
    for key, s in existing.items():
        if key not in desired:
            db.delete(s)  # событие убрали при правке результата
    for (pid, kind), (reason, n) in desired.items():
        if (pid, kind) not in existing:
            db.add(Sanction(player_id=pid, reason=reason, term="", matches=n, source_match_id=match.id, auto=kind))

def sync_yellow_accumulation(db: Session, match: Match, player_ids, skip_players=()) -> None:
    """Каждые 3 жёлтые за турнир = дисквалификация на 1 матч (автоматически)."""
    for pid in {p for p in player_ids if p and p not in skip_players}:
        player = db.query(Player).filter(Player.id == pid).first()
        if not player: continue
        expected = player.yellow_cards // YELLOWS_PER_BAN
        autos = db.query(Sanction).filter(Sanction.player_id == pid, Sanction.auto == "yellows").order_by(Sanction.id).all()
        for _ in range(expected - len(autos)):
            db.add(Sanction(player_id=pid, reason=YELLOWS_REASON, term="", matches=1, source_match_id=match.id, auto="yellows"))
        for s in autos[expected:]:
            db.delete(s)

def backfill_cards_and_sanctions(db: Session) -> None:
    """Однократно при обновлении со старой версии: пересчитать карточки (вторая жёлтая = красная),
    перевести пробные текстовые сроки вида «3 матча» в матчи и создать автосанкции по уже сыгранным матчам."""
    ids = {pid for (pid,) in db.query(MatchEvent.player_id).distinct()}
    ids |= {pid for (pid,) in db.query(Match.man_of_the_match_id).filter(Match.man_of_the_match_id.isnot(None)).distinct()}
    recompute_player_stats(db, ids)
    db.flush()
    sanctioned = {pid for (pid,) in db.query(Sanction.player_id).distinct()}  # у кого санкцию уже внёс админ — не дублируем
    for s in db.query(Sanction).filter(Sanction.auto.is_(None), Sanction.matches.is_(None), Sanction.until_date.is_(None)).all():
        m = re.fullmatch(r"\s*(\d{1,2})\s*(?:матч\w*)?\s*", s.term or "", flags=re.IGNORECASE)
        if m and int(m.group(1)) > 0:
            s.matches = int(m.group(1))
            s.term = ""
            player = db.query(Player).filter(Player.id == s.player_id).first()
            s.source_match_id = latest_finished_match_id(db, player.team_id if player else None)
    for match in db.query(Match).filter(Match.status == "finished").all():
        sync_auto_sanctions(db, match, skip_players=sanctioned)
    db.flush()
    for p in db.query(Player).filter(Player.yellow_cards >= YELLOWS_PER_BAN).all():
        last = db.query(MatchEvent.match_id).filter(MatchEvent.player_id == p.id).order_by(MatchEvent.match_id.desc()).first()
        if last:
            sync_yellow_accumulation(db, db.query(Match).filter(Match.id == last[0]).first(), [p.id], skip_players=sanctioned)

# ---------------------------------------------------------------------------
# Дата и время матчей. Оргкомитет назначает их на каждую неделю (обычно во вторник).
# ---------------------------------------------------------------------------
RU_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
RU_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MATCH_SLOT_MINUTES = 45  # 40 минут игры + перерыв: два матча в одном зале ближе этого — предупреждение

def match_when(m: Match) -> dict:
    """Дата/время матча для API. Если время не назначено — пустые строки и date_tbd=True."""
    if m.starts_at:
        d = m.starts_at
        return {"date": f"{d.day} {RU_MONTHS[d.month - 1]}", "time": d.strftime("%H:%M"), "weekday": RU_WEEKDAYS[d.weekday()],
                "starts_at": d.isoformat(timespec="minutes"), "date_tbd": False}
    legacy_date, legacy_time = (m.match_date or "").strip(), (m.match_time or "").strip()
    return {"date": legacy_date, "time": legacy_time, "weekday": "", "starts_at": None, "date_tbd": not (legacy_date or legacy_time)}

class ScheduleItem(BaseModel):
    match_id: int
    match_date: Optional[date] = None   # YYYY-MM-DD; пусто = дата не назначена
    match_time: Optional[str] = None    # HH:MM

class ScheduleUpdate(BaseModel):
    items: List[ScheduleItem]

@app.post("/api/matches/schedule", dependencies=[Depends(require_admin)])
def set_matches_schedule(data: ScheduleUpdate, db: Session = Depends(get_db)):
    """Назначить (или снять) дату и время матчей. Сыгранные и аннулированные матчи не меняются."""
    changed = []
    for it in data.items:
        m = db.query(Match).filter(Match.id == it.match_id).first()
        if not m:
            raise HTTPException(status_code=404, detail=f"Матч {it.match_id} не найден")
        if m.status == "finished" or m.annulled:
            continue
        t = (it.match_time or "").strip()
        if it.match_date is None and not t:
            m.starts_at, m.match_date, m.match_time = None, "", ""   # назначение снято
            continue
        mt = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", t)
        if it.match_date is None or not mt:
            raise HTTPException(status_code=400, detail=f"{m.home_team.name} — {m.away_team.name}: укажите и дату, и время (ЧЧ:ММ)")
        m.starts_at = datetime(it.match_date.year, it.match_date.month, it.match_date.day, int(mt.group(1)), int(mt.group(2)))
        m.match_date = f"{m.starts_at.day} {RU_MONTHS[m.starts_at.month - 1]}"
        m.match_time = m.starts_at.strftime("%H:%M")
        changed.append(m)
    db.flush()

    # Предупреждения: в зале одновременно идёт один матч (не блокируем — решает организатор)
    warnings, seen = [], set()
    dated = db.query(Match).filter(Match.starts_at.isnot(None), Match.status == "scheduled", Match.annulled == False).all()  # noqa: E712
    for m in changed:
        for o in dated:
            pair = frozenset((m.id, o.id))
            if o.id == m.id or pair in seen:
                continue
            if abs((o.starts_at - m.starts_at).total_seconds()) < MATCH_SLOT_MINUTES * 60:
                seen.add(pair)
                warnings.append(f"{m.starts_at.strftime('%d.%m')}: «{m.home_team.name} — {m.away_team.name}» ({m.starts_at.strftime('%H:%M')}) и "
                                f"«{o.home_team.name} — {o.away_team.name}» ({o.starts_at.strftime('%H:%M')}) идут почти одновременно")
    db.commit()
    return {"status": "success", "updated": len(changed), "warnings": warnings}

# Результаты: все матчи (график туров)
@app.get("/api/matches")
def get_matches(db: Session = Depends(get_db)):
    matches = db.query(Match).all()
    result = []
    for m in matches:
        events_list = [{
            "player_id": ev.player_id,
            "player_name": ev.player.name if ev.player else "",
            "team_id": ev.team_id,
            "event_type": ev.event_type,
            "effective_type": etype,  # second_yellow = вторая жёлтая, ставшая красной
            "minute": ev.minute
        } for ev, etype in effective_events(sorted(m.events, key=lambda e: e.id))]

        result.append({
            "id": m.id,
            "league_id": m.league_id,
            "league": m.league.name,
            "round": m.round_name,
            **match_when(m),
            "home_team_id": m.home_team_id,
            "home_team": m.home_team.name,
            "home_logo": m.home_team.logo_url,
            "away_team_id": m.away_team_id,
            "away_team": m.away_team.name,
            "away_logo": m.away_team.logo_url,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "status": m.status,
            "technical": bool(m.technical),
            "tech_reason": m.tech_reason,
            "annulled": bool(m.annulled),
            "man_of_the_match": m.man_of_the_match.name if m.man_of_the_match else None,
            "events": events_list
        })
    return result

@app.post("/api/matches/{match_id}/result", dependencies=[Depends(require_admin)])
def submit_match_result(
    match_id: int,
    data: MatchResultUpdate,
    db: Session = Depends(get_db)
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match: raise HTTPException(status_code=404, detail="Матч не найден")
    if match.annulled:
        raise HTTPException(status_code=400, detail="Матч аннулирован (команда снята с турнира). Чтобы изменить, верните команду.")

    if data.technical:
        # Технический результат: только 5:0 или 0:5, без событий и «Игрока матча»
        if (data.home_score, data.away_score) not in ((5, 0), (0, 5)):
            raise HTTPException(status_code=400, detail="Технический результат — только 5:0 или 0:5")
        data.events = []
        data.man_of_the_match_id = None

    validate_match_events(db, match, data.events)  # напр. три жёлтые одному игроку — ошибка, ничего не сохраняется

    # Игроки, чья статистика могла измениться (старые и новые события, игрок матча)
    affected = {ev.player_id for ev in match.events}
    if match.man_of_the_match_id: affected.add(match.man_of_the_match_id)
    if data.man_of_the_match_id: affected.add(data.man_of_the_match_id)
    affected |= {ev.player_id for ev in data.events}

    match.home_score = data.home_score
    match.away_score = data.away_score
    match.technical = data.technical
    match.tech_reason = None  # результат ввёл админ — это уже не автоматический результат снятия команды
    match.status = "finished"
    # Время первого внесения результата: от него считается голосование за тур.
    # При правке счёта оно не сдвигается, чтобы окно голосования не «прыгало».
    if match.finished_at is None:
        match.finished_at = now_utc().replace(tzinfo=None)
    match.man_of_the_match_id = data.man_of_the_match_id

    db.query(MatchEvent).filter(MatchEvent.match_id == match_id).delete()
    for ev in data.events:
        db.add(MatchEvent(match_id=match.id, player_id=ev.player_id, team_id=ev.team_id, event_type=ev.event_type, minute=ev.minute))
    db.flush()

    recompute_player_stats(db, affected)
    db.flush()
    sync_auto_sanctions(db, match)                 # вторая жёлтая -> красная -> дисквалификация на 1 матч
    sync_yellow_accumulation(db, match, affected)  # каждые 3 жёлтые за турнир -> 1 матч
    db.commit()

    sent_off = [ev.player_id for ev, t in effective_events(data.events) if t == "second_yellow"]
    message = "Результат сохранен!"
    if sent_off:
        names = ", ".join(p.name for p in db.query(Player).filter(Player.id.in_(sent_off)))
        message += f" Вторая жёлтая → красная, автоматическая дисквалификация на 1 матч: {names}."
    return {"status": "success", "message": message}

@app.post("/api/matches/{match_id}/reset", dependencies=[Depends(require_admin)])
def reset_match_result(match_id: int, db: Session = Depends(get_db)):
    """Вернуть матч в статус «не сыгран»: счёт 0:0, без событий и «Игрока матча»; статистика и автосанкции откатываются."""
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match: raise HTTPException(status_code=404, detail="Матч не найден")
    if match.annulled:
        raise HTTPException(status_code=400, detail="Матч аннулирован (команда снята с турнира)")

    affected = {ev.player_id for ev in match.events}
    if match.man_of_the_match_id: affected.add(match.man_of_the_match_id)

    db.query(MatchEvent).filter(MatchEvent.match_id == match_id).delete()
    match.status = "scheduled"
    match.home_score = match.away_score = 0
    match.technical = False
    match.tech_reason = None
    match.man_of_the_match_id = None
    match.finished_at = None  # при новом вводе результата голосование за тур отсчитается заново
    db.flush()

    # Автосанкции, созданные этим матчем, больше не нужны
    for s in db.query(Sanction).filter(Sanction.source_match_id == match_id, Sanction.auto.isnot(None)).all():
        db.delete(s)
    # Санкции, внесённые админом вручную, считались от этого матча — пересчитываем от последнего реально сыгранного
    for s in db.query(Sanction).filter(Sanction.source_match_id == match_id, Sanction.auto.is_(None)).all():
        s.source_match_id = latest_finished_match_id(db, s.player.team_id if s.player else None)
    db.flush()

    recompute_player_stats(db, affected)
    db.flush()
    sync_yellow_accumulation(db, match, affected)  # снимает «накопительные» санкции, если жёлтых стало меньше
    db.commit()
    return {"status": "success", "message": "Матч возвращён в статус «не сыгран»"}

# ---------------------------------------------------------------------------
# Снятие команды с турнира (регламент, ст. 3.13)
# - сыграла менее 50% запланированных матчей: все её матчи АННУЛИРУЮТСЯ (результаты, голы, карточки у обеих сторон);
# - сыграла 50% и более: сыгранные результаты сохраняются, в оставшихся матчах ей засчитывается 0:5, сопернику 5:0.
# Всё обратимо: «вернуть команду» восстанавливает матчи.
# ---------------------------------------------------------------------------
class WithdrawIn(BaseModel):
    note: str = Field("", max_length=500)  # решение Оргкомитета

def _withdrawal_plan(db: Session, team: Team) -> dict:
    ms = db.query(Match).filter(Match.annulled == False, (Match.home_team_id == team.id) | (Match.away_team_id == team.id)).all()  # noqa: E712
    total = len(ms)
    played = sum(1 for m in ms if m.status == "finished")
    remaining = sum(1 for m in ms if m.status == "scheduled")
    mode = "annul" if played * 2 < total else "technical"
    return {"matches": ms, "total": total, "played": played, "remaining": remaining, "mode": mode,
            "percent": round(100 * played / total) if total else 0}

@app.get("/api/teams/{team_id}/withdraw-preview", dependencies=[Depends(require_admin)])
def withdraw_preview(team_id: int, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team: raise HTTPException(status_code=404, detail="Команда не найдена")
    plan = _withdrawal_plan(db, team)
    return {"team": team.name, "withdrawn": bool(team.withdrawn), "played": plan["played"], "total": plan["total"],
            "remaining": plan["remaining"], "percent": plan["percent"], "mode": plan["mode"]}

@app.post("/api/teams/{team_id}/withdraw", dependencies=[Depends(require_admin)])
def withdraw_team(team_id: int, data: WithdrawIn, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team: raise HTTPException(status_code=404, detail="Команда не найдена")
    if team.withdrawn: raise HTTPException(status_code=400, detail="Команда уже снята с турнира")
    plan = _withdrawal_plan(db, team)
    affected, last_match = set(), None

    if plan["mode"] == "annul":
        for m in plan["matches"]:
            affected |= {ev.player_id for ev in m.events}
            if m.man_of_the_match_id: affected.add(m.man_of_the_match_id)
            m.annulled = True
            last_match = m
            for s in db.query(Sanction).filter(Sanction.source_match_id == m.id, Sanction.auto.isnot(None)).all():
                db.delete(s)  # карточки аннулированных матчей не влекут дисквалификаций
    else:
        now = now_utc().replace(tzinfo=None)
        for m in plan["matches"]:
            if m.status != "scheduled": continue
            team_is_home = m.home_team_id == team.id
            m.status, m.technical, m.tech_reason, m.finished_at = "finished", True, "withdrawal", now
            m.home_score, m.away_score = (0, 5) if team_is_home else (5, 0)
            last_match = m

    team.withdrawn, team.withdrawal_note = True, (data.note or "").strip() or None
    db.flush()
    recompute_player_stats(db, affected)
    db.flush()
    if last_match is not None:
        sync_yellow_accumulation(db, last_match, affected)
    db.commit()
    text = ("Все матчи команды аннулированы (сыграно менее 50%)." if plan["mode"] == "annul"
            else f"Результаты сыгранных матчей сохранены, в оставшихся {plan['remaining']} засчитаны технические поражения 0:5.")
    return {"status": "success", "mode": plan["mode"], "message": f"Команда «{team.name}» снята с турнира. {text}"}

@app.post("/api/teams/{team_id}/restore", dependencies=[Depends(require_admin)])
def restore_team(team_id: int, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team: raise HTTPException(status_code=404, detail="Команда не найдена")
    if not team.withdrawn: raise HTTPException(status_code=400, detail="Команда не снята с турнира")
    team.withdrawn, team.withdrawal_note = False, None
    affected, restored = set(), []
    ms = db.query(Match).filter((Match.home_team_id == team.id) | (Match.away_team_id == team.id)).all()
    for m in ms:
        other = m.away_team if m.home_team_id == team.id else m.home_team
        if m.annulled:
            if other is not None and other.withdrawn:
                # Соперник тоже снят. Если он снят с аннулированием, у него аннулированы и матчи с третьими командами —
                # тогда матч остаётся аннулированным (по его снятию)
                other_annulled_elsewhere = db.query(Match).filter(
                    Match.annulled == True,  # noqa: E712
                    (Match.home_team_id == other.id) | (Match.away_team_id == other.id),
                    Match.home_team_id != team.id, Match.away_team_id != team.id).count() > 0
                if other_annulled_elsewhere:
                    continue
            m.annulled = False
            affected |= {ev.player_id for ev in m.events}
            if m.man_of_the_match_id: affected.add(m.man_of_the_match_id)
            restored.append(m)
        elif m.tech_reason == "withdrawal":
            loser_is_home = (m.home_score or 0) == 0 and (m.away_score or 0) > 0
            loser_id = m.home_team_id if loser_is_home else m.away_team_id
            if loser_id == team.id:   # технический результат, назначенный из-за снятия именно этой команды — отменяем
                m.status, m.technical, m.tech_reason, m.finished_at = "scheduled", False, None, None
                m.home_score = m.away_score = 0
    db.flush()
    recompute_player_stats(db, affected)
    db.flush()
    for m in restored:
        if m.status == "finished":
            sync_auto_sanctions(db, m)
    if restored:
        sync_yellow_accumulation(db, restored[-1], affected)
    db.commit()
    return {"status": "success", "message": f"Команда «{team.name}» возвращена в турнир"}

@app.get("/api/matches/{match_id}/suspended")
def get_suspended_players(match_id: int, db: Session = Depends(get_db)):
    """Игроки двух команд, которые по действующим санкциям не имеют права играть в этом матче
    (участие дисквалифицированного = техническое поражение 0:5, регламент ст. 3.9)."""
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match: raise HTTPException(status_code=404, detail="Матч не найден")
    this_round = round_no(match.round_name)
    result = []
    for team_id in {match.home_team_id, match.away_team_id}:
        team_matches = db.query(Match).filter((Match.home_team_id == team_id) | (Match.away_team_id == team_id)).all()
        for s in db.query(Sanction).join(Player, Sanction.player_id == Player.id).filter(Player.team_id == team_id).order_by(Sanction.id):
            src = db.query(Match).filter(Match.id == s.source_match_id).first() if s.source_match_id else None
            if src is not None and not is_after(match, src):
                continue  # матч раньше (или тот же), в котором получена карточка
            if not s.matches:
                continue  # срок не в матчах (дата, текст, «уточняется») — какие матчи пропущены, определить нельзя
            # сколько матчей команды между карточкой и этим матчем: срок распространяется на первые N
            between = sum(1 for m in team_matches if m.id != match.id and (src is None or is_after(m, src)) and round_no(m.round_name) < this_round)
            if between >= s.matches:
                continue
            _, term_text, _served = sanction_state(db, s, team_id, now_utc().astimezone(TOURNAMENT_TZ).date())
            result.append({"player_id": s.player_id, "player_name": s.player.name, "team_id": team_id,
                           "reason": s.reason or "", "term_text": term_text})
    return result

@app.get("/api/leagues/{league_id}/standings")
def get_league_standings(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if not league: raise HTTPException(status_code=404, detail="Лига не найдена")

    standings = {}
    for team in league.teams:
        standings[team.id] = {
            "team_id": team.id, "team_name": team.name, "logo_url": team.logo_url,
            "played": 0, "wins": 0, "draws": 0, "losses": 0,
            "goals_for": 0, "goals_against": 0, "goal_difference": 0, "points": 0,
            "red_cards": 0, "yellow_cards": 0, "withdrawn": bool(team.withdrawn)
        }
        for p in team.players:
            standings[team.id]["red_cards"] += p.red_cards
            standings[team.id]["yellow_cards"] += p.yellow_cards

    finished_matches = db.query(Match).filter(Match.league_id == league_id, Match.status == "finished", Match.annulled == False).all()  # noqa: E712
    for m in finished_matches:
        if m.home_team_id in standings and m.away_team_id in standings:
            h, a = standings[m.home_team_id], standings[m.away_team_id]
            h["played"] += 1; a["played"] += 1
            h["goals_for"] += m.home_score; h["goals_against"] += m.away_score
            a["goals_for"] += m.away_score; a["goals_against"] += m.home_score

            if m.home_score > m.away_score: h["wins"] += 1; h["points"] += 3; a["losses"] += 1
            elif m.home_score < m.away_score: a["wins"] += 1; a["points"] += 3; h["losses"] += 1
            else: h["draws"] += 1; h["points"] += 1; a["draws"] += 1; a["points"] += 1

    for team_id in standings:
        standings[team_id]["goal_difference"] = standings[team_id]["goals_for"] - standings[team_id]["goals_against"]

    # До начала турнира — строго по алфавиту
    has_started = any(item["played"] > 0 for item in standings.values())
    if not has_started:
        ordered = sorted(standings.values(), key=lambda x: norm(x["team_name"]))
    else:
        ordered = sort_standings(list(standings.values()), finished_matches)
    # Снятые с аннулированием (ни одного учтённого матча) — в конец таблицы
    return [r for r in ordered if not (r["withdrawn"] and r["played"] == 0)] + [r for r in ordered if r["withdrawn"] and r["played"] == 0]

def head_to_head_table(team_ids: set, matches) -> dict:
    """Мини-таблица только по матчам между командами team_ids."""
    table = {tid: {"points": 0, "gd": 0, "gf": 0} for tid in team_ids}
    for m in matches:
        if m.home_team_id in table and m.away_team_id in table and m.home_team_id != m.away_team_id:
            h, a = table[m.home_team_id], table[m.away_team_id]
            hs, as_ = m.home_score or 0, m.away_score or 0
            h["gf"] += hs; a["gf"] += as_
            h["gd"] += hs - as_; a["gd"] += as_ - hs
            if hs > as_: h["points"] += 3
            elif hs < as_: a["points"] += 3
            else: h["points"] += 1; a["points"] += 1
    return table

def sort_standings(rows: list, finished_matches) -> list:
    """Регламент: 1) очки 2) РМ 3) победы 4) ЗМ 5) личные встречи (очки, РМ, ЗМ)
    6) меньше красных 7) меньше жёлтых 8) алфавит."""
    def main_key(r):
        return (-r["points"], -r["goal_difference"], -r["wins"], -r["goals_for"])

    result = []
    for _, group in groupby(sorted(rows, key=main_key), key=main_key):
        group = list(group)
        if len(group) > 1:
            h2h = head_to_head_table({r["team_id"] for r in group}, finished_matches)
            group.sort(key=lambda r: (
                -h2h[r["team_id"]]["points"],
                -h2h[r["team_id"]]["gd"],
                -h2h[r["team_id"]]["gf"],
                r["red_cards"],
                r["yellow_cards"],
                norm(r["team_name"]),
            ))
        result.extend(group)
    return result

@app.get("/api/leagues/{league_id}/top-scorers")
def get_top_scorers(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if not league: raise HTTPException(status_code=404, detail="Лига не найдена")
    team_ids = [t.id for t in league.teams]
    scorers = db.query(Player).filter(Player.team_id.in_(team_ids), (Player.goals > 0) | (Player.own_goals > 0)).order_by(Player.goals.desc()).all()
    return [{
        "player_id": p.id, "player_name": p.name, "team_id": p.team.id, "team_name": p.team.name,
        "goals": p.goals, "own_goals": p.own_goals
    } for p in scorers]

@app.get("/api/leagues/{league_id}/disqualification")
def get_disqualifications(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if not league: raise HTTPException(status_code=404, detail="Лига не найдена")
    team_ids = [t.id for t in league.teams]
    today = now_utc().astimezone(TOURNAMENT_TZ).date()

    by_player = {}
    for s in db.query(Sanction).join(Player, Sanction.player_id == Player.id).filter(Player.team_id.in_(team_ids)).order_by(Sanction.id):
        player = s.player
        active, term_text, served = sanction_state(db, s, player.team_id, today)
        if not active:
            continue  # срок прошёл — игрок исчезает из списка
        entry = by_player.setdefault(player.id, {
            "player_id": player.id, "player_name": player.name, "team_id": player.team.id, "team_name": player.team.name,
            "yellow_cards": player.yellow_cards, "red_cards": player.red_cards, "reason": "", "sanctions": []})
        entry["sanctions"].append({
            "id": s.id, "reason": s.reason or "", "term": s.term or "", "term_text": term_text,
            "matches": s.matches, "served": served, "until_date": s.until_date.isoformat() if s.until_date else None,
            "auto": s.auto,
        })
    return sorted(by_player.values(), key=lambda x: norm(x["player_name"]))

class SanctionIn(BaseModel):
    player_id: Optional[int] = None   # обязателен только при создании
    reason: str = ""                  # причина удаления
    term: str = ""                    # срок текстом (без автоматического окончания)
    matches: Optional[int] = Field(None, ge=1, le=99)  # срок в матчах: после них игрок исчезает из списка
    until_date: Optional[date] = None                  # или срок датой: исчезает на следующий день после неё

def _clean_sanction(data: SanctionIn):
    reason, term = data.reason.strip(), data.term.strip()
    if not (reason or term or data.matches or data.until_date):
        raise HTTPException(status_code=400, detail="Укажите причину удаления или срок дисквалификации")
    if data.matches and data.until_date:
        raise HTTPException(status_code=400, detail="Срок задаётся либо в матчах, либо датой — не обоими сразу")
    if len(reason) > 500 or len(term) > 100:
        raise HTTPException(status_code=400, detail="Слишком длинный текст (причина — до 500 символов, срок — до 100)")
    return reason, term

@app.post("/api/sanctions", dependencies=[Depends(require_admin)])
def create_sanction(data: SanctionIn, db: Session = Depends(get_db)):
    reason, term = _clean_sanction(data)
    player = db.query(Player).filter(Player.id == data.player_id).first() if data.player_id is not None else None
    if not player:
        raise HTTPException(status_code=404, detail="Игрок не найден")
    s = Sanction(player_id=player.id, reason=reason, term=term, matches=data.matches, until_date=data.until_date)
    if data.matches:
        s.source_match_id = latest_finished_match_id(db, player.team_id)  # срок считается с матчей после последнего сыгранного
    db.add(s); db.commit(); db.refresh(s)
    return {"status": "success", "id": s.id}

@app.put("/api/sanctions/{sanction_id}", dependencies=[Depends(require_admin)])
def update_sanction(sanction_id: int, data: SanctionIn, db: Session = Depends(get_db)):
    reason, term = _clean_sanction(data)
    s = db.query(Sanction).filter(Sanction.id == sanction_id).first()
    if not s: raise HTTPException(status_code=404, detail="Санкция не найдена")
    s.reason, s.term, s.matches, s.until_date = reason, term, data.matches, data.until_date
    if data.matches and s.source_match_id is None:
        s.source_match_id = latest_finished_match_id(db, s.player.team_id if s.player else None)
    db.commit()
    return {"status": "success"}

@app.delete("/api/sanctions/{sanction_id}", dependencies=[Depends(require_admin)])
def delete_sanction(sanction_id: int, db: Session = Depends(get_db)):
    s = db.query(Sanction).filter(Sanction.id == sanction_id).first()
    if not s: raise HTTPException(status_code=404, detail="Санкция не найдена")
    db.delete(s); db.commit()
    return {"status": "success"}

# ---------------------------------------------------------------------------
# Голосование за «Игрока тура»
# Тур лиги считается завершённым, когда сыграны все его матчи. Голосование открывается в первый
# понедельник 00:00 (по времени турнира) после завершения тура и длится 24 часа (до вторника 00:00).
# Кандидаты — «Игроки матча» этого тура. Один голос от пользователя Telegram на тур, изменить нельзя.
# ---------------------------------------------------------------------------
TOURNAMENT_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))
VOTING_DURATION = timedelta(hours=24)
DEV_VOTER_ID = -1  # локальная отладка без Telegram (только ALLOW_DEV_ADMIN + запрос с этого компьютера)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def voting_window(completed_at_utc: datetime):
    """Окно голосования для тура, завершённого в completed_at_utc (aware UTC)."""
    local = completed_at_utc.astimezone(TOURNAMENT_TZ)
    days_ahead = (7 - local.weekday()) % 7 or 7  # ближайший понедельник строго после дня завершения
    opens = (local + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    return opens, opens + VOTING_DURATION

def get_voter_id(request: Request, init_data: Optional[str]) -> Optional[int]:
    user = verify_telegram_init_data(init_data)
    if user and isinstance(user.get("id"), int):
        return user["id"]
    if ALLOW_DEV_ADMIN and is_local_request(request):
        return DEV_VOTER_ID
    return None

def build_league_polls(db: Session, league_id: int, voter_id: Optional[int]) -> list:
    matches = db.query(Match).filter(Match.league_id == league_id, Match.annulled == False).order_by(Match.id).all()  # noqa: E712
    rounds = {}
    for m in matches:
        rounds.setdefault(m.round_name or "Тур", []).append(m)

    # Туры, которые админ закрыл для голосования, хотя часть матчей не сыграна (например, перенесена)
    closures = {c.round_name: c.closed_at.replace(tzinfo=timezone.utc)
                for c in db.query(RoundClosure).filter(RoundClosure.league_id == league_id)}

    now = now_utc()
    polls = []
    for round_name, round_matches in rounds.items():
        unfinished = any(m.status != "finished" for m in round_matches)
        if unfinished and round_name not in closures:
            continue  # тур ещё не завершён
        motm_match = {}  # player_id -> матч, где он признан лучшим
        for m in round_matches:
            if m.man_of_the_match_id:
                motm_match[m.man_of_the_match_id] = m
        if not motm_match:
            continue  # нет «Игроков матча» — не из кого выбирать

        stamps = [m.finished_at.replace(tzinfo=timezone.utc) for m in round_matches if m.finished_at]
        if unfinished:
            stamps.append(closures[round_name])   # закрыт админом — отсчёт от момента закрытия
        completed_at = max(stamps) if stamps else now
        opens, closes = voting_window(completed_at)
        status = "upcoming" if now < opens else ("active" if now < closes else "closed")

        poll = {
            "league_id": league_id, "round": round_name, "status": status,
            "opens_at": opens.isoformat(), "closes_at": closes.isoformat(),
            "candidates_count": len(motm_match),
            "candidates": [], "total_votes": 0, "my_vote": None, "can_vote": False, "results_visible": False,
        }
        if status != "upcoming":
            counts = dict(db.query(PlayerVote.player_id, func.count(PlayerVote.id))
                          .filter(PlayerVote.league_id == league_id, PlayerVote.round_name == round_name)
                          .group_by(PlayerVote.player_id).all())
            my_vote = None
            if voter_id is not None:
                v = db.query(PlayerVote).filter(PlayerVote.league_id == league_id, PlayerVote.round_name == round_name,
                                                PlayerVote.voter_id == voter_id).first()
                my_vote = v.player_id if v else None
            total = sum(counts.values())
            reveal = status == "closed" or my_vote is not None  # итоги видны после своего голоса или по окончании
            top = max(counts.values()) if counts else 0

            candidates = []
            for pid, m in motm_match.items():
                p = db.query(Player).filter(Player.id == pid).first()
                if not p:
                    continue
                c = {
                    "player_id": p.id, "name": p.name, "number": p.number, "photo_url": p.photo_url,
                    "team_id": p.team_id, "team_name": p.team.name if p.team else "", "team_logo": p.team.logo_url if p.team else "",
                    "match": f"{m.home_team.name} {m.home_score}:{m.away_score} {m.away_team.name}",
                }
                if reveal:
                    c["votes"] = counts.get(pid, 0)
                    c["percent"] = round(100 * c["votes"] / total) if total else 0
                    c["is_winner"] = status == "closed" and top > 0 and c["votes"] == top
                candidates.append(c)
            candidates.sort(key=(lambda c: (-c["votes"], norm(c["name"]))) if reveal else (lambda c: norm(c["name"])))

            poll.update(candidates=candidates, total_votes=total, my_vote=my_vote, results_visible=reveal,
                        can_vote=(status == "active" and voter_id is not None and my_vote is None))
        polls.append(poll)

    order = {"active": 0, "upcoming": 1, "closed": 2}
    polls.reverse()  # свежие туры выше
    polls.sort(key=lambda p: order[p["status"]])  # sort устойчивый: внутри статуса порядок «свежие выше» сохраняется
    return polls

# ---------------------------------------------------------------------------
# Настройки пользователя: «моя команда», язык, уведомления (по подписи Telegram)
# ---------------------------------------------------------------------------
class MeSettings(BaseModel):
    favorite_team_id: Optional[int] = None
    clear_favorite: bool = False          # снять «мою команду»
    notifications: Optional[bool] = None
    lang: Optional[str] = None            # ru | kk

def _me_payload(sub: Optional[Subscriber], identified: bool) -> dict:
    if not sub:
        return {"identified": identified, "favorite_team_id": None, "notifications": True, "lang": None}
    return {"identified": identified, "favorite_team_id": sub.favorite_team_id, "notifications": bool(sub.notifications), "lang": sub.lang}

@app.get("/api/me")
def get_me(request: Request, x_telegram_init_data: Optional[str] = Header(None), db: Session = Depends(get_db)):
    uid = get_voter_id(request, x_telegram_init_data)
    if uid is None:
        return _me_payload(None, False)
    return _me_payload(db.query(Subscriber).filter(Subscriber.tg_user_id == uid).first(), True)

@app.post("/api/me/settings")
def update_me(data: MeSettings, request: Request, x_telegram_init_data: Optional[str] = Header(None), db: Session = Depends(get_db)):
    uid = get_voter_id(request, x_telegram_init_data)
    if uid is None:
        raise HTTPException(status_code=403, detail="Настройки сохраняются только при входе из Telegram")
    if data.lang is not None and data.lang not in ("ru", "kk"):
        raise HTTPException(status_code=400, detail="Неизвестный язык")
    if data.favorite_team_id is not None and not db.query(Team).filter(Team.id == data.favorite_team_id).first():
        raise HTTPException(status_code=404, detail="Команда не найдена")
    sub = db.query(Subscriber).filter(Subscriber.tg_user_id == uid).first()
    if not sub:
        user = verify_telegram_init_data(x_telegram_init_data) or {}
        sub = Subscriber(tg_user_id=uid, chat_id=uid, first_name=(user.get("first_name") or "")[:100])  # в личном чате chat_id = id пользователя
        db.add(sub)
    if data.clear_favorite: sub.favorite_team_id = None
    elif data.favorite_team_id is not None: sub.favorite_team_id = data.favorite_team_id
    if data.notifications is not None: sub.notifications = data.notifications
    if data.lang is not None: sub.lang = data.lang
    db.commit()
    return _me_payload(sub, True)

@app.get("/api/leagues/{league_id}/rounds")
def get_league_rounds(league_id: int, db: Session = Depends(get_db)):
    """Туры лиги: сколько матчей сыграно и закрыт ли тур для голосования."""
    if not db.query(League).filter(League.id == league_id).first():
        raise HTTPException(status_code=404, detail="Лига не найдена")
    closed = {c.round_name for c in db.query(RoundClosure).filter(RoundClosure.league_id == league_id)}
    rounds = {}
    for m in db.query(Match).filter(Match.league_id == league_id, Match.annulled == False).order_by(Match.id):  # noqa: E712
        r = rounds.setdefault(m.round_name or "Тур", {"round": m.round_name or "Тур", "total": 0, "finished": 0})
        r["total"] += 1
        r["finished"] += 1 if m.status == "finished" else 0
    return sorted(({**r, "closed": r["round"] in closed} for r in rounds.values()), key=lambda r: round_no(r["round"]))

@app.post("/api/leagues/{league_id}/rounds/{round_name}/close", dependencies=[Depends(require_admin)])
def close_round_for_voting(league_id: int, round_name: str, db: Session = Depends(get_db)):
    """Закрыть тур для голосования, если часть матчей перенесена: голосование пойдёт по сыгранным матчам."""
    if not db.query(Match).filter(Match.league_id == league_id, Match.round_name == round_name, Match.annulled == False).first():  # noqa: E712
        raise HTTPException(status_code=404, detail="Тур не найден")
    if not db.query(RoundClosure).filter(RoundClosure.league_id == league_id, RoundClosure.round_name == round_name).first():
        db.add(RoundClosure(league_id=league_id, round_name=round_name, closed_at=now_utc().replace(tzinfo=None)))
        db.commit()
    return {"status": "success", "message": f"{round_name}: тур закрыт для голосования"}

@app.delete("/api/leagues/{league_id}/rounds/{round_name}/close", dependencies=[Depends(require_admin)])
def reopen_round(league_id: int, round_name: str, db: Session = Depends(get_db)):
    db.query(RoundClosure).filter(RoundClosure.league_id == league_id, RoundClosure.round_name == round_name).delete()
    db.commit()
    return {"status": "success", "message": f"{round_name}: тур снова открыт"}

@app.get("/api/leagues/{league_id}/polls")
def get_league_polls(league_id: int, request: Request, x_telegram_init_data: Optional[str] = Header(None),
                     db: Session = Depends(get_db)):
    if not db.query(League).filter(League.id == league_id).first():
        raise HTTPException(status_code=404, detail="Лига не найдена")
    voter_id = get_voter_id(request, x_telegram_init_data)
    return {"polls": build_league_polls(db, league_id, voter_id), "can_identify_voter": voter_id is not None,
            "server_time": now_utc().isoformat()}

class VoteCreate(BaseModel):
    league_id: int
    round_name: str
    player_id: int

@app.post("/api/polls/vote")
def cast_vote(data: VoteCreate, request: Request, x_telegram_init_data: Optional[str] = Header(None),
              db: Session = Depends(get_db)):
    voter_id = get_voter_id(request, x_telegram_init_data)
    if voter_id is None:
        raise HTTPException(status_code=403, detail="Голосовать можно только из Telegram")

    poll = next((p for p in build_league_polls(db, data.league_id, voter_id) if p["round"] == data.round_name), None)
    if not poll:
        raise HTTPException(status_code=404, detail="Голосование не найдено")
    if poll["status"] == "upcoming":
        raise HTTPException(status_code=403, detail="Голосование ещё не началось")
    if poll["status"] == "closed":
        raise HTTPException(status_code=403, detail="Голосование уже завершено")
    if poll["my_vote"] is not None:
        raise HTTPException(status_code=409, detail="Вы уже проголосовали в этом туре")
    if data.player_id not in {c["player_id"] for c in poll["candidates"]}:
        raise HTTPException(status_code=400, detail="Этот игрок не участвует в голосовании")

    db.add(PlayerVote(league_id=data.league_id, round_name=data.round_name, player_id=data.player_id, voter_id=voter_id))
    try:
        db.commit()
    except IntegrityError:  # параллельный повторный голос
        db.rollback()
        raise HTTPException(status_code=409, detail="Вы уже проголосовали в этом туре")
    updated = next(p for p in build_league_polls(db, data.league_id, voter_id) if p["round"] == data.round_name)
    return {"status": "success", "message": "Голос принят!", "poll": updated}

@app.get("/api/teams/{team_id}")
def get_team_profile(team_id: int, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team: raise HTTPException(status_code=404, detail="Команда не найдена")
    
    finished_matches = db.query(Match).filter((Match.home_team_id == team_id) | (Match.away_team_id == team_id), Match.status == "finished", Match.annulled == False).all()  # noqa: E712
    
    played, wins, draws, losses, goals_for, goals_against, points = len(finished_matches), 0, 0, 0, 0, 0, 0
    form_results = []
    for m in finished_matches:
        is_home = m.home_team_id == team_id
        my_score, opp_score = (m.home_score, m.away_score) if is_home else (m.away_score, m.home_score)
        goals_for += my_score; goals_against += opp_score
        if my_score > opp_score: wins += 1; points += 3; form_results.append("W")
        elif my_score == opp_score: draws += 1; points += 1; form_results.append("D")
        else: losses += 1; form_results.append("L")

    all_team_matches = db.query(Match).filter((Match.home_team_id == team_id) | (Match.away_team_id == team_id)).all()
    all_team_matches.sort(key=lambda m: (round_no(m.round_name), m.id))
    matches_list = []
    for m in all_team_matches:
        is_home = m.home_team_id == team_id
        opponent = m.away_team if is_home else m.home_team
        matches_list.append({
            "id": m.id,
            "round": m.round_name,
            **match_when(m),
            "status": m.status,
            "is_home": is_home,
            "opponent_name": opponent.name if opponent else "Соперник",
            "opponent_logo": opponent.logo_url if opponent else "",
            "home_score": m.home_score,
            "away_score": m.away_score,
            "technical": bool(m.technical),
            "annulled": bool(m.annulled)
        })

    return {
        "id": team.id, "name": team.name, "logo_url": team.logo_url,
        "withdrawn": bool(team.withdrawn), "withdrawal_note": team.withdrawal_note,
        "stats": {
            "played": played, "wins": wins, "draws": draws, "losses": losses,
            "goals_for": goals_for, "goals_against": goals_against,
            "goal_difference": goals_for - goals_against, "points": points,
            "yellow_cards": sum(p.yellow_cards for p in team.players),
            "red_cards": sum(p.red_cards for p in team.players),
            "form": form_results[-5:]
        },
        "matches": matches_list,
        "players": [{
            "id": p.id, "name": p.name, "number": p.number, "position": p.position,
            "photo_url": p.photo_url,
            "stats": {
                "goals": p.goals, "own_goals": p.own_goals,
                "yellow_cards": p.yellow_cards, "red_cards": p.red_cards, 
                "man_of_the_match": p.man_of_the_match_count
            }
        } for p in team.players]
    }