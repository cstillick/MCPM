from typing import List, Optional
from pydantic import BaseModel, Field


# ── Auth ────────────────────────────────────────────────────────────────────

class LoginForm(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)
    is_admin: bool = False


# ── Players ─────────────────────────────────────────────────────────────────

class PlayerEloEntry(BaseModel):
    name: str
    elo: float
    total_wins: Optional[int] = None
    shirt_swap_count: Optional[int] = None
    total_games: Optional[int] = None
    total_races: Optional[int] = None
    head_to_head: Optional[dict] = None  # {"OpponentName": {"wins": int, "losses": int}}


class EloImport(BaseModel):
    players: List[PlayerEloEntry]


# ── Games ────────────────────────────────────────────────────────────────────

class TeamInput(BaseModel):
    name: str
    player1_id: int
    player2_id: int


class GameCreate(BaseModel):
    name: str
    teams: List[TeamInput] = Field(..., min_items=4, max_items=4)


# ── Bets ─────────────────────────────────────────────────────────────────────

class PlaceBet(BaseModel):
    market_id: int
    option_id: int
    coins_wagered: int = Field(..., gt=0)


# ── Admin ────────────────────────────────────────────────────────────────────

class AddCoins(BaseModel):
    user_id: int
    amount: int = Field(..., gt=0)


class SettleMarket(BaseModel):
    market_id: int
    winning_option_id: int


class RaceResultEntry(BaseModel):
    player_id: int
    placement: int = Field(..., ge=1, le=8)


class RaceResultsSubmit(BaseModel):
    results: List[RaceResultEntry]
