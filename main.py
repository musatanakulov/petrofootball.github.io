from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from database import SessionLocal, init_db, League, Team, Player, Match, MatchEvent
from pydantic import BaseModel
from typing import List, Optional
import docx
import io
import os
import shutil

app = FastAPI(title="Кубок Акима города Петропавловск API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LOGO_DIR = "static/logos"
os.makedirs(LOGO_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def startup_event():
    init_db()
    db = SessionLocal()
    leagues_data = ["Первая лига", "Вторая лига", "Третья лига"]
    for l_name in leagues_data:
        existing_league = db.query(League).filter(League.name == l_name).first()
        if not existing_league:
            db.add(League(name=l_name))
    db.commit()
    
    all_leagues = db.query(League).all()
    for l in all_leagues:
        if l.name not in leagues_data:
            db.delete(l)
    db.commit()
    db.close()


# --- Pydantic Схемы ---
class TeamUpdate(BaseModel):
    new_name: str

class MatchEventCreate(BaseModel):
    player_id: int
    team_id: int
    event_type: str  # "goal", "own_goal", "yellow_card", "red_card"
    minute: Optional[int] = None

class MatchResultUpdate(BaseModel):
    home_score: int
    away_score: int
    man_of_the_match_id: Optional[int] = None
    events: List[MatchEventCreate] = []


# --- Эндпоинты ---
@app.get("/api/leagues")
def get_leagues(db: Session = Depends(get_db)):
    leagues = db.query(League).all()
    result = []
    for league in leagues:
        teams_list = [{
            "id": t.id,
            "name": t.name,
            "logo_url": t.logo_url
        } for t in league.teams]
        
        result.append({
            "id": league.id,
            "name": league.name,
            "teams": teams_list
        })
    return result

@app.post("/api/upload-roster")
def upload_team_roster(
    league_name: str = Form(...),
    team_name: str = Form(...),
    file: UploadFile = File(...),
    logo: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    league = db.query(League).filter(League.name == league_name).first()
    if not league:
        raise HTTPException(status_code=400, detail="Неверное название лиги")

    logo_url = "https://via.placeholder.com/80"
    if logo:
        logo_filename = f"{team_name}_{logo.filename}".replace(" ", "_")
        logo_path = os.path.join(LOGO_DIR, logo_filename)
        with open(logo_path, "wb") as buffer:
            shutil.copyfileobj(logo.file, buffer)
        logo_url = f"http://127.0.0.1:8000/static/logos/{logo_filename}"

    team = db.query(Team).filter(Team.name == team_name, Team.league_id == league.id).first()
    if not team:
        team = Team(name=team_name, logo_url=logo_url, league_id=league.id)
        db.add(team)
        db.commit()
        db.refresh(team)
    else:
        team.logo_url = logo_url
        db.commit()

    try:
        contents = file.file.read()
        doc = docx.Document(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения файла Word: {str(e)}")

    added_players_count = 0
    for table in doc.tables:
        for row in table.rows[1:]:
            cells = [cell.text.strip() for cell in row.cells]
            if len(cells) >= 2:
                row_num = cells[0]
                p_name = cells[1]
                if row_num.isdigit() and p_name and p_name != "Ф И О":
                    existing_player = db.query(Player).filter(Player.name == p_name, Player.team_id == team.id).first()
                    if not existing_player:
                        player = Player(name=p_name, number=int(row_num), position="Игрок", team_id=team.id)
                        db.add(player)
                        added_players_count += 1
    db.commit()

    return {"status": "success", "message": f"Команда '{team_name}' успешно загружена!", "added_players": added_players_count}

@app.post("/api/upload-schedule")
def upload_schedule(
    league_name: str = Form(...),
    round_name: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    league = db.query(League).filter(League.name == league_name).first()
    if not league:
        raise HTTPException(status_code=400, detail="Лига не найдена")

    try:
        contents = file.file.read()
        doc = docx.Document(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка чтения файла: {str(e)}")

    added_matches_count = 0
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if len(cells) >= 4:
                row_num = cells[0]
                home_name = cells[1]
                datetime_raw = cells[2]
                away_name = cells[3]
                
                if not row_num.isdigit() or home_name in ["Команда 1", ""] or away_name in ["Команда 2", ""]:
                    continue

                dt_parts = datetime_raw.split()
                match_date = " ".join(dt_parts[:-1]) if len(dt_parts) > 1 else "21 ноября"
                match_time = dt_parts[-1].replace(".", ":") if dt_parts else "15:00"

                home_team = db.query(Team).filter(Team.name == home_name, Team.league_id == league.id).first()
                if not home_team:
                    home_team = Team(name=home_name, logo_url="https://via.placeholder.com/80", league_id=league.id)
                    db.add(home_team)
                    db.commit()
                    db.refresh(home_team)

                away_team = db.query(Team).filter(Team.name == away_name, Team.league_id == league.id).first()
                if not away_team:
                    away_team = Team(name=away_name, logo_url="https://via.placeholder.com/80", league_id=league.id)
                    db.add(away_team)
                    db.commit()
                    db.refresh(away_team)

                match = Match(
                    league_id=league.id,
                    round_name=round_name,
                    match_date=match_date,
                    match_time=match_time,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    status="scheduled"
                )
                db.add(match)
                added_matches_count += 1
    db.commit()

    return {"status": "success", "message": f"Загружено матчей: {added_matches_count}"}

@app.get("/api/matches")
def get_matches(db: Session = Depends(get_db)):
    matches = db.query(Match).all()
    result = []
    for m in matches:
        result.append({
            "id": m.id,
            "league": m.league.name,
            "round": m.round_name,
            "date": m.match_date,
            "time": m.match_time,
            "home_team": m.home_team.name,
            "home_logo": m.home_team.logo_url,
            "away_team": m.away_team.name,
            "away_logo": m.away_team.logo_url,
            "home_score": m.home_score,
            "away_score": m.away_score,
            "status": m.status,
            "man_of_the_match": m.man_of_the_match.name if m.man_of_the_match else None
        })
    return result

@app.post("/api/matches/{match_id}/result")
def submit_match_result(match_id: int, data: MatchResultUpdate, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Матч не найден")

    # Сбрасываем старую статистику событий при перезаписи протокола
    old_events = db.query(MatchEvent).filter(MatchEvent.match_id == match_id).all()
    for ev in old_events:
        player = db.query(Player).filter(Player.id == ev.player_id).first()
        if player:
            if ev.event_type == "goal":
                player.goals = max(0, player.goals - 1)
            elif ev.event_type == "yellow_card":
                player.yellow_cards = max(0, player.yellow_cards - 1)
            elif ev.event_type == "red_card":
                player.red_cards = max(0, player.red_cards - 1)
    
    if match.man_of_the_match_id:
        old_motm = db.query(Player).filter(Player.id == match.man_of_the_match_id).first()
        if old_motm:
            old_motm.man_of_the_match_count = max(0, old_motm.man_of_the_match_count - 1)

    # Присваиваем новые данные матча
    match.home_score = data.home_score
    match.away_score = data.away_score
    match.status = "finished"
    match.man_of_the_match_id = data.man_of_the_match_id

    if data.man_of_the_match_id:
        motm_player = db.query(Player).filter(Player.id == data.man_of_the_match_id).first()
        if motm_player:
            motm_player.man_of_the_match_count += 1

    # Удаляем старые события
    db.query(MatchEvent).filter(MatchEvent.match_id == match_id).delete()

    # Записываем новые события
    for ev in data.events:
        event = MatchEvent(
            match_id=match.id, player_id=ev.player_id,
            team_id=ev.team_id, event_type=ev.event_type, minute=ev.minute
        )
        db.add(event)

        player = db.query(Player).filter(Player.id == ev.player_id).first()
        if player:
            if ev.event_type == "goal":
                player.goals += 1
            elif ev.event_type == "yellow_card":
                player.yellow_cards += 1
            elif ev.event_type == "red_card":
                player.red_cards += 1

    db.commit()
    return {"status": "success", "message": "Результат, протокол и Игрок матча успешно сохранены!"}

@app.get("/api/leagues/{league_id}/standings")
def get_league_standings(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="Лига не найдена")

    standings = {}
    for team in league.teams:
        standings[team.id] = {
            "team_id": team.id, 
            "team_name": team.name, 
            "logo_url": team.logo_url,
            "played": 0, "wins": 0, "draws": 0, "losses": 0,
            "goals_for": 0, "goals_against": 0, "goal_difference": 0, "points": 0,
            "red_cards": 0, "yellow_cards": 0
        }
        for p in team.players:
            standings[team.id]["red_cards"] += p.red_cards
            standings[team.id]["yellow_cards"] += p.yellow_cards

    finished_matches = db.query(Match).filter(Match.league_id == league_id, Match.status == "finished").all()
    has_finished_matches = len(finished_matches) > 0

    for m in finished_matches:
        if m.home_team_id in standings and m.away_team_id in standings:
            h = standings[m.home_team_id]
            a = standings[m.away_team_id]
            h["played"] += 1; a["played"] += 1
            h["goals_for"] += m.home_score; h["goals_against"] += m.away_score
            a["goals_for"] += m.away_score; a["goals_against"] += m.home_score

            if m.home_score > m.away_score:
                h["wins"] += 1; h["points"] += 3; a["losses"] += 1
            elif m.home_score < m.away_score:
                a["wins"] += 1; a["points"] += 3; h["losses"] += 1
            else:
                h["draws"] += 1; h["points"] += 1; a["draws"] += 1; a["points"] += 1

    for team_id in standings:
        standings[team_id]["goal_difference"] = standings[team_id]["goals_for"] - standings[team_id]["goals_against"]

    if not has_finished_matches:
        sorted_standings = sorted(standings.values(), key=lambda x: x["team_name"])
    else:
        sorted_standings = sorted(
            standings.values(),
            key=lambda x: (
                x["points"], 
                x["goal_difference"], 
                x["wins"], 
                x["goals_for"], 
                -x["red_cards"], 
                -x["yellow_cards"]
            ),
            reverse=True
        )

    return sorted_standings

@app.get("/api/leagues/{league_id}/top-scorers")
def get_top_scorers(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="Лига не найдена")
    team_ids = [t.id for t in league.teams]
    scorers = db.query(Player).filter(Player.team_id.in_(team_ids), Player.goals > 0).order_by(Player.goals.desc()).all()
    return [{"player_name": p.name, "team_name": p.team.name, "goals": p.goals} for p in scorers]

@app.get("/api/leagues/{league_id}/disqualification")
def get_disqualifications(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="Лига не найдена")
    team_ids = [t.id for t in league.teams]
    players = db.query(Player).filter(Player.team_id.in_(team_ids), (Player.yellow_cards > 0) | (Player.red_cards > 0)).all()

    disqualified_list = []
    for p in players:
        reasons = []
        if p.yellow_cards >= 3:
            reasons.append(f"Перебор желтых ({p.yellow_cards})")
        if p.red_cards > 0:
            reasons.append(f"Красная карточка / КДК ({p.red_cards})")
        if reasons:
            disqualified_list.append({
                "player_name": p.name, "team_name": p.team.name,
                "yellow_cards": p.yellow_cards, "red_cards": p.red_cards,
                "reason": ", ".join(reasons)
            })
    return disqualified_list

@app.get("/api/teams/{team_id}")
def get_team_profile(team_id: int, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    return {
        "id": team.id, "name": team.name, "logo_url": team.logo_url,
        "players": [{
            "id": p.id, "name": p.name, "number": p.number, "position": p.position,
            "stats": {"goals": p.goals, "assists": p.assists, "yellow_cards": p.yellow_cards, "red_cards": p.red_cards, "man_of_the_match": p.man_of_the_match_count}
        } for p in team.players]
    }

@app.get("/api/players/{player_id}")
def get_player_profile(player_id: int, db: Session = Depends(get_db)):
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Игрок не найден")
    
    return {
        "id": player.id,
        "name": player.name,
        "number": player.number,
        "position": player.position,
        "photo_url": player.photo_url or "https://via.placeholder.com/150",
        "team_name": player.team.name,
        "stats": {
            "goals": player.goals,
            "assists": player.assists,
            "yellow_cards": player.yellow_cards,
            "red_cards": player.red_cards,
            "man_of_the_match": player.man_of_the_match_count
        }
    }

@app.put("/api/teams/{team_id}/rename")
def rename_team(team_id: int, data: TeamUpdate, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    team.name = data.new_name
    db.commit()
    return {"status": "success", "message": f"Переименовано в '{data.new_name}'"}