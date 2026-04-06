import json
from typing import List

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException,
    Request, UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from database import get_db
from datetime import datetime

from models import (
    Bet, BetMarket, BetOption, CoinTransaction, Game, HeadToHead,
    PendingRegistration, Player, Race, RaceResult, Team, User,
)
from auth import hash_password
from template_env import templates

router = APIRouter(prefix="/admin")


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    games = db.query(Game).order_by(Game.created_at.desc()).all()
    users = db.query(User).order_by(User.username).all()
    pending_count = db.query(PendingRegistration).count()
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {"request": request, "user": admin, "games": games, "users": users, "pending_count": pending_count},
    )


# ── User management ──────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    users = db.query(User).order_by(User.username).all()
    pending = db.query(PendingRegistration).order_by(PendingRegistration.created_at).all()
    return templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "user": admin, "users": users, "pending": pending},
    )


@router.post("/users/approve/{pending_id}")
def approve_user(request: Request, pending_id: int, db: Session = Depends(get_db)):
    require_admin(request, db)
    pending = db.query(PendingRegistration).filter(PendingRegistration.id == pending_id).first()
    if not pending:
        raise HTTPException(status_code=404, detail="Pending registration not found")
    if db.query(User).filter(User.username == pending.username).first():
        db.delete(pending)
        db.commit()
        raise HTTPException(status_code=400, detail="Username already exists as an active user")
    db.add(User(username=pending.username, password_hash=pending.password_hash, is_admin=False, coin_balance=1000))
    db.delete(pending)
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/reject/{pending_id}")
def reject_user(request: Request, pending_id: int, db: Session = Depends(get_db)):
    require_admin(request, db)
    pending = db.query(PendingRegistration).filter(PendingRegistration.id == pending_id).first()
    if not pending:
        raise HTTPException(status_code=404, detail="Pending registration not found")
    db.delete(pending)
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/create")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: bool = Form(False),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=is_admin,
        coin_balance=1000,
    )
    db.add(user)
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/add-coins")
def add_coins(
    request: Request,
    user_id: int = Form(...),
    amount: int = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    target.coin_balance += amount
    db.add(CoinTransaction(
        user_id=target.id,
        bet_id=None,
        type="admin_grant",
        description="Admin coin grant",
        coins_wagered=None,
        net_amount=amount,
    ))
    db.commit()
    return RedirectResponse("/admin/users", status_code=302)


# ── Game creation ─────────────────────────────────────────────────────────────

@router.get("/games/create", response_class=HTMLResponse)
def create_game_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    players = db.query(Player).order_by(Player.elo.desc()).all()
    return templates.TemplateResponse(
        "admin/game_form.html",
        {"request": request, "user": admin, "players": players},
    )


@router.post("/games/create")
def create_game(
    request: Request,
    game_name: str = Form(...),
    # 4 teams × 2 players: team1_name, team1_p1, team1_p2, ...
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
    require_admin(request, db)

    game = Game(name=game_name, status="upcoming")
    db.add(game)
    db.flush()  # get game.id

    team_data = [
        (team1_name, team1_p1, team1_p2),
        (team2_name, team2_p1, team2_p2),
        (team3_name, team3_p1, team3_p2),
        (team4_name, team4_p1, team4_p2),
    ]

    # Validate no player appears twice
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
        avg_elo = (p1.elo + p2.elo) / 2.0
        team = Team(game_id=game.id, name=name, player1_id=p1_id, player2_id=p2_id, average_elo=avg_elo)
        db.add(team)
        teams.append((team, p1, p2))

    db.flush()

    # Create 16 races
    for i in range(1, 17):
        race = Race(game_id=game.id, race_number=i, status="pending")
        db.add(race)

    db.flush()

    # Auto-create pre-game bet markets
    # 1. Team win market
    team_win_market = BetMarket(
        game_id=game.id,
        race_id=None,
        market_type="team_win",
        description="Which team wins the game?",
        status="open",
    )
    db.add(team_win_market)
    db.flush()

    for team, _, _ in teams:
        db.add(BetOption(market_id=team_win_market.id, label=team.name))

    # 2. ELO direction market per player
    all_players_in_game = [(p1, p2) for _, p1, p2 in teams]
    seen_players = set()
    for p1, p2 in all_players_in_game:
        for player in (p1, p2):
            if player.id in seen_players:
                continue
            seen_players.add(player.id)
            market = BetMarket(
                game_id=game.id,
                race_id=None,
                market_type="elo_direction",
                description=f"Will {player.name} gain or lose ELO?",
                status="open",
            )
            db.add(market)
            db.flush()
            db.add(BetOption(market_id=market.id, label="Gain"))
            db.add(BetOption(market_id=market.id, label="Lose"))

    db.commit()
    return RedirectResponse(f"/games/{game.id}", status_code=302)


# ── Game status management ────────────────────────────────────────────────────

@router.post("/games/{game_id}/set-status")
def set_game_status(
    game_id: int,
    request: Request,
    new_status: str = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if new_status not in ("upcoming", "live", "completed"):
        raise HTTPException(status_code=400, detail="Invalid status")

    if new_status == "live":
        # Close all pre-game markets so no more bets
        db.query(BetMarket).filter(
            BetMarket.game_id == game_id,
            BetMarket.race_id == None,
            BetMarket.status == "open",
        ).update({"status": "closed"})

    game.status = new_status
    db.commit()
    return RedirectResponse(f"/admin/games/{game_id}", status_code=302)


# ── Race management ───────────────────────────────────────────────────────────

@router.get("/games/{game_id}", response_class=HTMLResponse)
def admin_game_detail(game_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    pregame_markets = db.query(BetMarket).filter(
        BetMarket.game_id == game_id, BetMarket.race_id == None
    ).all()

    return templates.TemplateResponse(
        "admin/game_detail.html",
        {
            "request": request,
            "user": admin,
            "game": game,
            "pregame_markets": pregame_markets,
        },
    )


@router.post("/games/{game_id}/races/{race_id}/open-betting")
def open_race_betting(
    game_id: int,
    race_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    race = db.query(Race).filter(Race.id == race_id, Race.game_id == game_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")
    if race.status != "pending":
        raise HTTPException(status_code=400, detail="Race is not in pending status")

    game = db.query(Game).filter(Game.id == game_id).first()

    # Create per-race markets
    # 1. Race winner (who gets 1st place)
    winner_market = BetMarket(
        game_id=game_id,
        race_id=race_id,
        market_type="race_winner",
        description=f"Who wins Race {race.race_number}?",
        status="open",
    )
    db.add(winner_market)
    db.flush()

    # Add all 8 players as options
    players_in_game = []
    for team in game.teams:
        players_in_game.extend([team.player1, team.player2])

    for player in players_in_game:
        db.add(BetOption(market_id=winner_market.id, label=player.name))

    # 2. Shirt swap market per player (will they finish top 2?)
    for player in players_in_game:
        shirt_market = BetMarket(
            game_id=game_id,
            race_id=race_id,
            market_type="shirt_swap",
            description=f"Will {player.name} shirt swap (top 2) in Race {race.race_number}?",
            status="open",
        )
        db.add(shirt_market)
        db.flush()
        db.add(BetOption(market_id=shirt_market.id, label="Yes"))
        db.add(BetOption(market_id=shirt_market.id, label="No"))

    race.status = "betting_open"
    db.commit()
    return RedirectResponse(f"/admin/games/{game_id}", status_code=302)


@router.get("/games/{game_id}/races/{race_id}/results", response_class=HTMLResponse)
def race_results_page(
    game_id: int,
    race_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    race = db.query(Race).filter(Race.id == race_id, Race.game_id == game_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    game = db.query(Game).filter(Game.id == game_id).first()
    players_in_game = []
    for team in game.teams:
        players_in_game.extend([team.player1, team.player2])

    return templates.TemplateResponse(
        "admin/race_results.html",
        {
            "request": request,
            "user": admin,
            "game": game,
            "race": race,
            "players": players_in_game,
        },
    )


@router.post("/games/{game_id}/races/{race_id}/results")
async def submit_race_results(
    game_id: int,
    race_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    race = db.query(Race).filter(Race.id == race_id, Race.game_id == game_id).first()
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")

    game = db.query(Game).filter(Game.id == game_id).first()
    players_in_game = []
    for team in game.teams:
        players_in_game.extend([team.player1, team.player2])

    form = await request.form()

    placements = {}
    for player in players_in_game:
        key = f"player_{player.id}"
        val = form.get(key)
        if val is None:
            raise HTTPException(status_code=400, detail=f"Missing placement for {player.name}")
        try:
            placement = int(val)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid placement value")
        if placement < 1 or placement > 8:
            raise HTTPException(status_code=400, detail="Placement must be 1-8")
        placements[player.id] = placement

    # Validate unique placements
    if len(set(placements.values())) != len(placements):
        raise HTTPException(status_code=400, detail="Each placement must be unique (1-8)")

    # Save results
    for player_id, placement in placements.items():
        result = RaceResult(race_id=race_id, player_id=player_id, placement=placement)
        db.add(result)

    # Settle race markets
    winner_player_id = min(placements, key=lambda pid: placements[pid])
    winner_player = next(p for p in players_in_game if p.id == winner_player_id)

    for market in race.bet_markets:
        if market.market_type == "race_winner":
            winning_label = winner_player.name
            market.winning_outcome = winning_label
            market.status = "settled"
            _settle_market(db, market, winning_label)

        elif market.market_type == "shirt_swap":
            # Extract player name from description "Will [name] shirt swap..."
            player_name = market.description.split("Will ")[1].split(" shirt swap")[0]
            player_obj = next((p for p in players_in_game if p.name == player_name), None)
            if player_obj:
                did_shirt_swap = placements[player_obj.id] <= 2
                winning_label = "Yes" if did_shirt_swap else "No"
                market.winning_outcome = winning_label
                market.status = "settled"
                _settle_market(db, market, winning_label)

                # Update player shirt swap count
                if did_shirt_swap:
                    player_obj.shirt_swap_count += 1
                player_obj.total_races += 1

    race.status = "completed"
    db.commit()
    return RedirectResponse(f"/admin/games/{game_id}", status_code=302)


def _settle_market(db: Session, market: BetMarket, winning_label: str):
    """Distribute parimutuel payouts to winners."""
    winning_option = next((o for o in market.options if o.label == winning_label), None)
    if not winning_option:
        return

    total_pool = sum(o.total_coins_wagered for o in market.options)
    winning_pool = winning_option.total_coins_wagered

    if winning_pool == 0:
        return  # no winners, coins already deducted — house keeps

    now = datetime.utcnow()
    for bet in market.bets:
        txn = db.query(CoinTransaction).filter(CoinTransaction.bet_id == bet.id).first()
        if bet.option_id == winning_option.id:
            payout = int((bet.coins_wagered / winning_pool) * total_pool)
            bet.payout = payout
            bet.user.coin_balance += payout
            if txn:
                txn.type = "won"
                txn.net_amount = payout - bet.coins_wagered
                txn.settled_at = now
        else:
            bet.payout = 0
            if txn:
                txn.type = "lost"
                txn.settled_at = now


# ── ELO import ────────────────────────────────────────────────────────────────

@router.get("/elo-import", response_class=HTMLResponse)
def elo_import_page(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    return templates.TemplateResponse(
        "admin/elo_import.html",
        {"request": request, "user": admin, "message": None},
    )


@router.post("/elo-import")
async def elo_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return templates.TemplateResponse(
            "admin/elo_import.html",
            {"request": request, "user": get_current_user(request, db), "message": "Invalid JSON file"},
            status_code=400,
        )

    if not isinstance(data, list):
        return templates.TemplateResponse(
            "admin/elo_import.html",
            {"request": request, "user": get_current_user(request, db), "message": "JSON must be a list of player objects"},
            status_code=400,
        )

    updated = 0
    created = 0
    for entry in data:
        if not isinstance(entry, dict) or "name" not in entry or "elo" not in entry:
            continue

        player = db.query(Player).filter(Player.name == entry["name"]).first()
        if player:
            old_elo = player.elo
            player.elo = float(entry["elo"])
            if "total_wins" in entry:
                player.total_wins = int(entry["total_wins"])
            if "shirt_swap_count" in entry:
                player.shirt_swap_count = int(entry["shirt_swap_count"])
            if "total_games" in entry:
                player.total_games = int(entry["total_games"])
            if "total_races" in entry:
                player.total_races = int(entry["total_races"])
            updated += 1
        else:
            player = Player(
                name=entry["name"],
                elo=float(entry["elo"]),
                total_wins=int(entry.get("total_wins", 0)),
                shirt_swap_count=int(entry.get("shirt_swap_count", 0)),
                total_games=int(entry.get("total_games", 0)),
                total_races=int(entry.get("total_races", 0)),
            )
            db.add(player)
            db.flush()
            created += 1

        # Optional: import head-to-head data
        if "head_to_head" in entry and isinstance(entry["head_to_head"], dict):
            player_obj = db.query(Player).filter(Player.name == entry["name"]).first()
            for opp_name, record in entry["head_to_head"].items():
                opp = db.query(Player).filter(Player.name == opp_name).first()
                if not opp:
                    continue
                a_id = min(player_obj.id, opp.id)
                b_id = max(player_obj.id, opp.id)
                h2h = db.query(HeadToHead).filter(
                    HeadToHead.player_a_id == a_id,
                    HeadToHead.player_b_id == b_id,
                ).first()
                if not h2h:
                    h2h = HeadToHead(player_a_id=a_id, player_b_id=b_id)
                    db.add(h2h)
                    db.flush()
                if player_obj.id == a_id:
                    h2h.wins_a = int(record.get("wins", h2h.wins_a))
                    h2h.wins_b = int(record.get("losses", h2h.wins_b))
                else:
                    h2h.wins_b = int(record.get("wins", h2h.wins_b))
                    h2h.wins_a = int(record.get("losses", h2h.wins_a))

    db.commit()

    # Settle any open elo_direction markets for completed games
    _settle_elo_markets(db)

    message = f"Import complete: {updated} updated, {created} created."
    return templates.TemplateResponse(
        "admin/elo_import.html",
        {"request": request, "user": get_current_user(request, db), "message": message},
    )


def _settle_elo_markets(db: Session):
    """
    Settle elo_direction markets for games that are completed.
    Compares current ELO against the pre-import snapshot stored in the market description.
    Since we don't store old ELOs, admins trigger settlement manually via the game admin page.
    This is a hook for future use.
    """
    pass


# ── Manual ELO market settlement ──────────────────────────────────────────────

@router.post("/games/{game_id}/settle-elo")
def settle_elo_markets(
    game_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Admin manually settles ELO direction markets after importing new ELOs.
    Reads current player ELOs and compares to ELO at game creation (stored in team.average_elo
    as a proxy — not perfect, but works for the market concept).

    For a proper solution: admin submits the old ELO JSON alongside the new one.
    For now: admin selects Gain/Lose for each player via a form.
    """
    require_admin(request, db)
    return RedirectResponse(f"/admin/games/{game_id}/settle-elo-form", status_code=302)


@router.get("/games/{game_id}/settle-elo-form", response_class=HTMLResponse)
def settle_elo_form(game_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    elo_markets = db.query(BetMarket).filter(
        BetMarket.game_id == game_id,
        BetMarket.market_type == "elo_direction",
        BetMarket.status.in_(["open", "closed"]),
    ).all()

    return templates.TemplateResponse(
        "admin/settle_elo.html",
        {"request": request, "user": admin, "game": game, "elo_markets": elo_markets},
    )


@router.post("/games/{game_id}/settle-elo-submit")
async def settle_elo_submit(game_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    form = await request.form()

    elo_markets = db.query(BetMarket).filter(
        BetMarket.game_id == game_id,
        BetMarket.market_type == "elo_direction",
        BetMarket.status.in_(["open", "closed"]),
    ).all()

    for market in elo_markets:
        key = f"market_{market.id}"
        outcome = form.get(key)
        if outcome not in ("Gain", "Lose"):
            continue
        market.winning_outcome = outcome
        market.status = "settled"
        _settle_market(db, market, outcome)

    db.commit()
    return RedirectResponse(f"/admin/games/{game_id}", status_code=302)
