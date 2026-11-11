from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from database import SessionLocal, init_db, League, Team, Player

app = FastAPI(title="Football Tournament API")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/api/leagues")
def get_leagues(db: Session = Depends(get_db)):
    return db.query(League).all()

@app.get("/api/teams/{team_id}")
def get_team_profile(team_id: int, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    
    # Возвращаем информацию о команде, ее логотипе, составе и статистике игроков
    return {
        "id": team.id,
        "name": team.name,
        "logo_url": team.logo_url,
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "number": p.number,
                "position": p.position,
                "stats": {
                    "goals": p.goals,
                    "assists": p.assists,
                    "yellow_cards": p.yellow_cards,
                    "red_cards": p.red_cards
                }
            } for p in team.players
        ]
    }