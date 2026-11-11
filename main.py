from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from database import SessionLocal, init_db, League, Team, Player

app = FastAPI(title="Petropavl Football Tournament API")

# Разрешаем запросы с любых источников (включая ваш GitHub Pages фронтенд)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    
    # Проверяем, есть ли лиги в базе данных
    if not db.query(League).first():
        leagues_data = ["Высшая лига", "Первая лига", "Вторая лига"]
        for l_name in leagues_data:
            league = League(name=l_name)
            db.add(league)
        db.commit()
        
        # Добавим тестовую команду в Высшую лигу для примера
        v_league = db.query(League).filter(League.name == "Высшая лига").first()
        if v_league:
            sample_team = Team(
                name="Динамо Петропавловск",
                logo_url="https://via.placeholder.com/80",
                league_id=v_league.id
            )
            db.add(sample_team)
            db.commit()
            
            # Добавим игроков
            p1 = Player(name="Иван Иванов", number=10, position="Нападающий", goals=5, assists=2, team_id=sample_team.id)
            p2 = Player(name="Алексей Петров", number=7, position="Полузащитник", goals=3, assists=4, team_id=sample_team.id)
            db.add_all([p1, p2])
            db.commit()
    db.close()

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

@app.get("/api/teams/{team_id}")
def get_team_profile(team_id: int, db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    
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