from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

SQLALCHEMY_DATABASE_URL = "sqlite:///./football_tournament.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class League(Base):
    __tablename__ = "leagues"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True) # Например: Высшая лига, Первая лига, Вторая лига
    teams = relationship("Team", back_populates="league")

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    logo_url = Column(String, nullable=True) # Ссылка на логотип команды
    league_id = Column(Integer, ForeignKey("leagues.id"))
    
    league = relationship("League", back_populates="teams")
    players = relationship("Player", back_populates="team")

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    number = Column(Integer)
    position = Column(String) # Вратарь, Защитник, Полузащитник, Нападающий
    team_id = Column(Integer, ForeignKey("teams.id"))
    
    # Индивидуальная статистика
    goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    yellow_cards = Column(Integer, default=0)
    red_cards = Column(Integer, default=0)
    
    team = relationship("Team", back_populates="players")

def init_db():
    Base.metadata.create_all(bind=engine)