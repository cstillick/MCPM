from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Bet, BetMarket, BetOption, Game, P2PBet, P2PBetEntry, Player, Race, RaceResult, SiteSettings, Team
from template_env import templates

router = APIRouter()


def elo_win_probability(elo_a: float, elo_b: float) -> float:
    """Expected score of A when playing against B (standard Elo formula)."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def compute_price_history(market) -> dict:
    """Replay bets in order to build per-option implied probability history."""
    bets_sorted = sorted(market.bets, key=lambda b: b.created_at)
    if not bets_sorted:
        return {}
    option_coins = {opt.id: 0 for opt in market.options}
    labels = []
    series = {opt.label: [] for opt in market.options}
    for i, bet in enumerate(bets_sorted):
        option_coins[bet.option_id] += bet.coins_wagered
        total = sum(option_coins.values())
        labels.append(i + 1)
        for opt in market.options:
            series[opt.label].append(round(option_coins[opt.id] / total * 100, 1))
    return {"labels": labels, "series": series}


def compute_team_win_probs(teams) -> dict:
    """
    Returns a dict of team_id -> win probability (normalized across all 4 teams).
    Uses average pairwise Elo expected scores between teams.
    """
    team_scores = {}
    for team in teams:
        team_elo = team.average_elo
        score = 0.0
        for other in teams:
            if other.id == team.id:
                continue
            score += elo_win_probability(team_elo, other.average_elo)
        team_scores[team.id] = score

    total = sum(team_scores.values()) or 1.0
    return {tid: round(s / total * 100, 1) for tid, s in team_scores.items()}


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    upcoming = db.query(Game).filter(Game.status == "upcoming").order_by(Game.created_at.desc()).all()
    live = db.query(Game).filter(Game.status == "live").order_by(Game.created_at.desc()).all()
    completed = db.query(Game).filter(Game.status == "completed").order_by(Game.created_at.desc()).all()
    settings = db.query(SiteSettings).first()
    p2p_enabled = bool(settings and settings.p2p_betting_enabled)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "upcoming": upcoming,
            "live": live,
            "completed": completed,
            "p2p_enabled": p2p_enabled,
        },
    )


@router.get("/games/create", response_class=HTMLResponse)
def user_create_game_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    settings = db.query(SiteSettings).first()
    if not (settings and settings.p2p_betting_enabled):
        raise HTTPException(status_code=403, detail="Game creation requires P2P betting to be enabled")
    players = db.query(Player).filter(Player.retired == False).order_by(Player.elo.desc()).all()
    return templates.TemplateResponse(
        "game_form.html",
        {"request": request, "user": user, "players": players},
    )


@router.post("/games/create")
def user_create_game(
    request: Request,
    game_name: str = Form(...),
    team1_name: str = Form(...),
    team1_p1: int = Form(...),
    team1_p2: int = Form(...),
    team2_name: str = Form(...),
    team2_p1: int = Form(...),
    team2_p2: int = Form(...),
    team3_name: str = Form(...),
    team3_p1: int = Form(...),
    team3_p2: int = Form(...),
    team4_name: str = Form(...),
    team4_p1: int = Form(...),
    team4_p2: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    settings = db.query(SiteSettings).first()
    if not (settings and settings.p2p_betting_enabled):
        raise HTTPException(status_code=403, detail="Game creation requires P2P betting to be enabled")

    game = Game(name=game_name, status="upcoming", created_by_user_id=user.id)
    db.add(game)
    db.flush()

    team_data = [
        (team1_name, team1_p1, team1_p2),
        (team2_name, team2_p1, team2_p2),
        (team3_name, team3_p1, team3_p2),
        (team4_name, team4_p1, team4_p2),
    ]

    all_player_ids = [pid for _, p1, p2 in team_data for pid in (p1, p2)]
    if len(all_player_ids) != len(set(all_player_ids)):
        db.rollback()
        raise HTTPException(status_code=400, detail="Each player can only be on one team")

    teams = []
    for name, p1_id, p2_id in team_data:
        p1 = db.query(Player).filter(Player.id == p1_id).first()
        p2 = db.query(Player).filter(Player.id == p2_id).first()
        if not p1 or not p2:
            db.rollback()
            raise HTTPException(status_code=400, detail="Invalid player ID")
        team = Team(game_id=game.id, name=name, player1_id=p1_id, player2_id=p2_id,
                    average_elo=(p1.elo + p2.elo) / 2.0)
        db.add(team)
        teams.append((team, p1, p2))

    db.flush()

    for i in range(1, 17):
        db.add(Race(game_id=game.id, race_number=i, status="pending"))

    db.flush()

    # Team win market
    from models import BetMarket as _BM, BetOption as _BO
    team_win_market = _BM(game_id=game.id, race_id=None, market_type="team_win",
                          description="Which team wins the game?", status="open")
    db.add(team_win_market)
    db.flush()
    for team, _, _ in teams:
        db.add(_BO(market_id=team_win_market.id, label=team.name))

    # ELO direction markets
    seen = set()
    for team, p1, p2 in teams:
        for player in (p1, p2):
            if player.id in seen:
                continue
            seen.add(player.id)
            m = _BM(game_id=game.id, race_id=None, market_type="elo_direction",
                    description=f"Will {player.name} gain or lose ELO?", status="open")
            db.add(m)
            db.flush()
            db.add(_BO(market_id=m.id, label="Gain"))
            db.add(_BO(market_id=m.id, label="Lose"))

    # Shirt swap markets
    for team, _, _ in teams:
        m = _BM(game_id=game.id, race_id=None, market_type="shirt_swap",
                description=f"Will {team.name} shirt swap? (top 2 teams by total points)",
                status="open")
        db.add(m)
        db.flush()
        db.add(_BO(market_id=m.id, label="Yes"))
        db.add(_BO(market_id=m.id, label="No"))

    db.commit()
    return RedirectResponse(f"/games/{game.id}", status_code=302)


@router.get("/games/{game_id}", response_class=HTMLResponse)
def game_detail(game_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    # Pre-game markets
    pregame_markets = (
        db.query(BetMarket)
        .filter(BetMarket.game_id == game_id, BetMarket.race_id == None)
        .all()
    )

    # User's existing bets on this game (pre-game)
    user_bets = {}
    if user:
        bets = (
            db.query(Bet)
            .join(BetMarket)
            .filter(BetMarket.game_id == game_id, Bet.user_id == user.id)
            .all()
        )
        for bet in bets:
            user_bets[bet.market_id] = bet

    # ELO-based win probabilities
    win_probs = compute_team_win_probs(game.teams) if game.teams else {}

    # Community consensus per pre-game team_win market
    community_probs = {}
    for m in pregame_markets:
        if m.market_type == "team_win":
            total = sum(o.total_coins_wagered for o in m.options) or 1
            for o in m.options:
                community_probs[o.id] = round(o.total_coins_wagered / total * 100, 1)

    price_histories = {m.id: compute_price_history(m) for m in pregame_markets}

    # P2P exchange
    settings = db.query(SiteSettings).first()
    p2p_enabled = bool(settings and settings.p2p_betting_enabled)
    p2p_bets = []
    user_p2p_entry_bet_ids = set()
    if p2p_enabled:
        p2p_bets = (
            db.query(P2PBet)
            .filter(P2PBet.game_id == game_id, P2PBet.status == "open")
            .order_by(P2PBet.created_at.desc())
            .all()
        )
        if user:
            joined = (
                db.query(P2PBetEntry.p2p_bet_id)
                .filter(P2PBetEntry.user_id == user.id)
                .filter(P2PBetEntry.p2p_bet_id.in_([b.id for b in p2p_bets]))
                .all()
            )
            user_p2p_entry_bet_ids = {row[0] for row in joined}

    return templates.TemplateResponse(
        "game_detail.html",
        {
            "request": request,
            "user": user,
            "game": game,
            "pregame_markets": pregame_markets,
            "user_bets": user_bets,
            "win_probs": win_probs,
            "community_probs": community_probs,
            "price_histories": price_histories,
            "p2p_enabled": p2p_enabled,
            "p2p_bets": p2p_bets,
            "user_p2p_entry_bet_ids": user_p2p_entry_bet_ids,
            "is_game_creator": bool(user and game.created_by_user_id == user.id),
        },
    )


@router.get("/games/{game_id}/race/{race_number}", response_class=HTMLResponse)
def race_detail(game_id: int, race_number: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    race = (
        db.query(Race)
        .filter(Race.game_id == game_id, Race.race_number == race_number)
        .first()
    )
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    race_markets = db.query(BetMarket).filter(BetMarket.race_id == race.id).all()

    user_bets = {}
    if user:
        bets = (
            db.query(Bet)
            .join(BetMarket)
            .filter(BetMarket.race_id == race.id, Bet.user_id == user.id)
            .all()
        )
        for bet in bets:
            user_bets[bet.market_id] = bet

    price_histories = {m.id: compute_price_history(m) for m in race_markets}

    return templates.TemplateResponse(
        "race_detail.html",
        {
            "request": request,
            "user": user,
            "game": game,
            "race": race,
            "race_markets": race_markets,
            "user_bets": user_bets,
            "price_histories": price_histories,
        },
    )
