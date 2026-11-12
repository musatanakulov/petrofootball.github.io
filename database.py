import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Date, Boolean, UniqueConstraint, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Тесты подставляют временную базу через DATABASE_URL; по умолчанию — рабочая база рядом с кодом
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./football_tournament.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Версия данных о карточках/санкциях: 1 = вторая жёлтая считается красной, санкции со сроком в матчах;
# 2 = две жёлтые в матче превращаются в красную и не остаются в счётчике жёлтых (однократный пересчёт при старте)
CARDS_SCHEMA_VERSION = 2

class League(Base):
    __tablename__ = "leagues"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)

    teams = relationship("Team", back_populates="league", cascade="all, delete-orphan")
    matches = relationship("Match", back_populates="league", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    logo_url = Column(String, default="https://via.placeholder.com/80")
    league_id = Column(Integer, ForeignKey("leagues.id"))
    withdrawn = Column(Boolean, default=False)       # команда снята/исключена (ст. 3.13)
    withdrawal_note = Column(String, nullable=True)  # решение Оргкомитета

    league = relationship("League", back_populates="teams")
    players = relationship("Player", back_populates="team", cascade="all, delete-orphan")


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    number = Column(Integer)
    position = Column(String, default="Игрок")
    photo_url = Column(String, default="https://via.placeholder.com/150")
    
    # Статистика игрока
    goals = Column(Integer, default=0)
    own_goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    yellow_cards = Column(Integer, default=0)
    red_cards = Column(Integer, default=0)
    man_of_the_match_count = Column(Integer, default=0)
    
    team_id = Column(Integer, ForeignKey("teams.id"))
    team = relationship("Team", back_populates="players")


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    round_name = Column(String)  
    match_date = Column(String)  
    match_time = Column(String)  
    
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    away_team_id = Column(Integer, ForeignKey("teams.id"))
    
    home_score = Column(Integer, default=0)
    away_score = Column(Integer, default=0)
    status = Column(String, default="scheduled")
    # Когда результат был внесён впервые (UTC, без tzinfo). От него считается начало голосования за тур.
    finished_at = Column(DateTime, nullable=True)
    technical = Column(Boolean, default=False)  # технический результат 5:0 / 0:5 (регламент 3.9–3.11)
    # Дата и время начала (местное время турнира, без tzinfo). Пусто, пока Оргкомитет не назначил (обычно во вторник).
    starts_at = Column(DateTime, nullable=True)
    # Матч аннулирован при снятии команды с турнира до 50% матчей (ст. 3.13): не входит в таблицу, статистику и голосование
    annulled = Column(Boolean, default=False)
    # Причина автоматического технического результата: "withdrawal" — соперник снялся (ст. 3.13)
    tech_reason = Column(String, nullable=True)

    man_of_the_match_id = Column(Integer, ForeignKey("players.id"), nullable=True)

    league = relationship("League", back_populates="matches")
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    man_of_the_match = relationship("Player", foreign_keys=[man_of_the_match_id])
    events = relationship("MatchEvent", back_populates="match", cascade="all, delete-orphan")


class MatchEvent(Base):
    __tablename__ = "match_events"

    id = Column(Integer, primary_key=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id"))
    player_id = Column(Integer, ForeignKey("players.id"))
    team_id = Column(Integer, ForeignKey("teams.id"))
    event_type = Column(String)
    minute = Column(Integer, nullable=True)

    match = relationship("Match", back_populates="events")
    player = relationship("Player")


class PlayerVote(Base):
    """Голос за «Игрока тура». Один голос от одного пользователя Telegram на тур лиги."""
    __tablename__ = "player_votes"
    __table_args__ = (UniqueConstraint("league_id", "round_name", "voter_id", name="uq_vote_per_round"),)

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), index=True)
    round_name = Column(String, index=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    voter_id = Column(Integer)  # Telegram user id (проверенный по подписи initData)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    player = relationship("Player")


class Subscriber(Base):
    """Пользователь Telegram: любимая команда, язык и согласие на уведомления от бота."""
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, index=True)
    tg_user_id = Column(Integer, unique=True, index=True)   # проверенный по подписи Telegram id
    chat_id = Column(Integer)                               # личный чат с ботом (совпадает с id пользователя)
    first_name = Column(String, default="")
    favorite_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    lang = Column(String, default="ru")                     # ru | kk
    notifications = Column(Boolean, default=True)           # напоминания о матчах и голосовании
    blocked = Column(Boolean, default=False)                # бот не может писать (пользователь его заблокировал)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class SentNotification(Base):
    """Что уже отправлено кому — чтобы не слать одно и то же дважды."""
    __tablename__ = "sent_notifications"
    __table_args__ = (UniqueConstraint("subscriber_id", "key", name="uq_sent_once"),)

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), index=True)
    key = Column(String)   # например "vote:1:Тур 3" или "match24:57"
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class RoundClosure(Base):
    """Админ закрыл тур для голосования, хотя часть матчей не сыграна (например, перенесена)."""
    __tablename__ = "round_closures"
    __table_args__ = (UniqueConstraint("league_id", "round_name", name="uq_round_closure"),)

    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), index=True)
    round_name = Column(String)
    closed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Sanction(Base):
    """Санкция КДК, которую вносит администратор: причина удаления и срок дисквалификации игрока."""
    __tablename__ = "sanctions"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), index=True)
    reason = Column(String, default="")  # причина удаления
    term = Column(String, default="")    # срок текстом (если он не в матчах и не датой); без автоматического окончания
    matches = Column(Integer, nullable=True)      # срок в матчах: игрок пропускает N следующих матчей своей команды
    until_date = Column(Date, nullable=True)      # срок датой: действует по эту дату включительно
    source_match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)  # от какого матча считается срок в матчах
    auto = Column(String, nullable=True)          # second_yellow | red | yellows — создана автоматически по событиям матча
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    player = relationship("Player")


def init_db() -> bool:
    """Создаёт таблицы и докидывает недостающие колонки.
    Возвращает True, если нужно один раз пересчитать карточки и автосанкции по уже сохранённым матчам
    (см. backfill_cards_and_sanctions). Признак «уже сделано» хранится в самой базе (PRAGMA user_version)."""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        existing = {c["name"] for c in inspect(conn).get_columns("sanctions")}
        for name, ddl in (("matches", "INTEGER"), ("until_date", "DATE"), ("source_match_id", "INTEGER"), ("auto", "VARCHAR")):
            if name not in existing:
                conn.execute(text(f"ALTER TABLE sanctions ADD COLUMN {name} {ddl}"))
        needs_backfill = (conn.execute(text("PRAGMA user_version")).scalar() or 0) < CARDS_SCHEMA_VERSION
    # create_all не добавляет колонки в уже существующие таблицы — делаем лёгкую миграцию вручную
    cols = {c["name"] for c in inspect(engine).get_columns("matches")}
    with engine.begin() as conn:
        if "finished_at" not in cols:
            conn.execute(text("ALTER TABLE matches ADD COLUMN finished_at DATETIME"))
        if "technical" not in cols:
            conn.execute(text("ALTER TABLE matches ADD COLUMN technical BOOLEAN DEFAULT 0"))
        for name, ddl in (("starts_at", "DATETIME"), ("annulled", "BOOLEAN DEFAULT 0"), ("tech_reason", "VARCHAR")):
            if name not in cols:
                conn.execute(text(f"ALTER TABLE matches ADD COLUMN {name} {ddl}"))
        team_cols = {c["name"] for c in inspect(conn).get_columns("teams")}
        for name, ddl in (("withdrawn", "BOOLEAN DEFAULT 0"), ("withdrawal_note", "VARCHAR")):
            if name not in team_cols:
                conn.execute(text(f"ALTER TABLE teams ADD COLUMN {name} {ddl}"))
        # Уже сыгранные матчи без отметки времени: считаем завершёнными на момент миграции
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute(text("UPDATE matches SET finished_at = :now WHERE status = 'finished' AND finished_at IS NULL"), {"now": now})
    return needs_backfill


def mark_backfill_done(db) -> None:
    db.execute(text(f"PRAGMA user_version = {CARDS_SCHEMA_VERSION}"))