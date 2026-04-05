from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    coin_balance = Column(Integer, default=1000)

    bets = relationship("Bet", back_populates="user")


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    elo = Column(Float, default=1000.0)
    total_wins = Column(Integer, default=0)
    shirt_swap_count = Column(Integer, default=0)
    total_games = Column(Integer, default=0)
    total_races = Column(Integer, default=0)


class HeadToHead(Base):
    __tablename__ = "head_to_head"

    id = Column(Integer, primary_key=True, index=True)
    player_a_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    player_b_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    wins_a = Column(Integer, default=0)
    wins_b = Column(Integer, default=0)

    player_a = relationship("Player", foreign_keys=[player_a_id])
    player_b = relationship("Player", foreign_keys=[player_b_id])

    __table_args__ = (UniqueConstraint("player_a_id", "player_b_id"),)


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    # upcoming | live | completed
    status = Column(String, default="upcoming", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    teams = relationship("Team", back_populates="game", order_by="Team.id")
    races = relationship("Race", back_populates="game", order_by="Race.race_number")
    bet_markets = relationship("BetMarket", back_populates="game")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    name = Column(String, nullable=False)
    player1_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    player2_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    average_elo = Column(Float, default=0.0)

    game = relationship("Game", back_populates="teams")
    player1 = relationship("Player", foreign_keys=[player1_id])
    player2 = relationship("Player", foreign_keys=[player2_id])


class Race(Base):
    __tablename__ = "races"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    race_number = Column(Integer, nullable=False)
    # pending | betting_open | completed
    status = Column(String, default="pending", nullable=False)

    game = relationship("Game", back_populates="races")
    results = relationship("RaceResult", back_populates="race", order_by="RaceResult.placement")
    bet_markets = relationship("BetMarket", back_populates="race")


class RaceResult(Base):
    __tablename__ = "race_results"

    id = Column(Integer, primary_key=True, index=True)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    placement = Column(Integer, nullable=False)  # 1–8

    race = relationship("Race", back_populates="results")
    player = relationship("Player")


class BetMarket(Base):
    __tablename__ = "bet_markets"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    race_id = Column(Integer, ForeignKey("races.id"), nullable=True)  # NULL = pre-game market
    # team_win | elo_direction | race_winner | shirt_swap | head_to_head
    market_type = Column(String, nullable=False)
    description = Column(String, nullable=False)
    # open | closed | settled
    status = Column(String, default="open", nullable=False)
    # label of the winning option, set when settling
    winning_outcome = Column(String, nullable=True)

    game = relationship("Game", back_populates="bet_markets")
    race = relationship("Race", back_populates="bet_markets")
    options = relationship("BetOption", back_populates="market", cascade="all, delete-orphan")
    bets = relationship("Bet", back_populates="market")


class BetOption(Base):
    __tablename__ = "bet_options"

    id = Column(Integer, primary_key=True, index=True)
    market_id = Column(Integer, ForeignKey("bet_markets.id"), nullable=False)
    label = Column(String, nullable=False)
    total_coins_wagered = Column(Integer, default=0)

    market = relationship("BetMarket", back_populates="options")
    bets = relationship("Bet", back_populates="option")


class Bet(Base):
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    market_id = Column(Integer, ForeignKey("bet_markets.id"), nullable=False)
    option_id = Column(Integer, ForeignKey("bet_options.id"), nullable=False)
    coins_wagered = Column(Integer, nullable=False)
    payout = Column(Integer, nullable=True)  # NULL until settled
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="bets")
    market = relationship("BetMarket", back_populates="bets")
    option = relationship("BetOption", back_populates="bets")
