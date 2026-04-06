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

# Points awarded per placement (only placements 1–4 score; others don't race)
POINTS_MAP = {1: 3, 2: 2, 3: 1, 4: 0}


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

    # 3. Shirt swap markets per team (game-level: top 2 teams by total points swap)
    for team, _, _ in teams:
        shirt_market = BetMarket(
            game_id=game.id,
            race_id=None,
            market_type="shirt_swap",
            description=f"Will {team.name} shirt swap? (top 2 teams by total points)",
            status="open",
        )
        db.add(shirt_market)
        db.flush()
        db.add(BetOption(market_id=shirt_market.id, label="Yes"))
        db.add(BetOption(market_id=shirt_market.id, label="No"))

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
            BetMarket.market_type != "shirt_swap",
            BetMarket.market_type != "over_under",
        ).update({"status": "closed"}, synchronize_session=False)

    if new_status == "completed":
        _settle_game_completion_markets(game_id, db)

    game.status = new_status
    db.commit()
    return RedirectResponse(f"/admin/games/{game_id}", status_code=302)


def _settle_game_completion_markets(game_id: int, db: Session):
    """Settle shirt_swap and over_under markets when a game is marked completed."""
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        return

    team_points = _get_team_points(game_id, db)
    player_points = _get_player_points(game_id, db)

    # Determine top-2 teams by total points
    sorted_teams = sorted(team_points.items(), key=lambda x: x[1], reverse=True)
    top2_team_ids = {t[0] for t in sorted_teams[:2]}

    # Settle shirt_swap markets
    shirt_markets = db.query(BetMarket).filter(
        BetMarket.game_id == game_id,
        BetMarket.market_type == "shirt_swap",
        BetMarket.status.in_(["open", "closed"]),
    ).all()

    for market in shirt_markets:
        # Market description: "Will [Team Name] shirt swap? ..."
        # Find the team by matching name in description
        team_name = market.description.split("Will ")[1].split(" shirt swap")[0]
        matched_team = next((t for t in game.teams if t.name == team_name), None)
        if matched_team is None:
            continue
        winning_label = "Yes" if matched_team.id in top2_team_ids else "No"
        market.winning_outcome = winning_label
        market.status = "settled"
        _settle_market(db, market, winning_label)

    # Update shirt_swap_count for players on top-2 teams
    for team in game.teams:
        if team.id in top2_team_ids:
            for player in (team.player1, team.player2):
                player.shirt_swap_count += 1

    # Update total_games and total_wins for all players
    # Find winning team(s) — the team with the most points
    if sorted_teams:
        max_pts = sorted_teams[0][1]
        winning_team_ids = {t[0] for t in sorted_teams if t[1] == max_pts}
    else:
        winning_team_ids = set()

    for team in game.teams:
        for player in (team.player1, team.player2):
            player.total_games += 1
            if team.id in winning_team_ids:
                player.total_wins += 1

    # Settle over_under markets
    ou_markets = db.query(BetMarket).filter(
        BetMarket.game_id == game_id,
        BetMarket.market_type == "over_under",
        BetMarket.status.in_(["open", "closed"]),
    ).all()

    for market in ou_markets:
        if market.threshold is None:
            continue
        if market.subject_team_id:
            actual = team_points.get(market.subject_team_id, 0)
        elif market.subject_player_id:
            actual = player_points.get(market.subject_player_id, 0)
        else:
            continue
        winning_label = "Over" if actual > market.threshold else "Under"
        market.winning_outcome = winning_label
        market.status = "settled"
        _settle_market(db, market, winning_label)


# ── Over/Under market creation ────────────────────────────────────────────────

@router.post("/games/{game_id}/markets/create-over-under")
def create_over_under_market(
    game_id: int,
    request: Request,
    subject_type: str = Form(...),   # "team" or "player"
    subject_id: int = Form(...),
    threshold: float = Form(...),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if subject_type not in ("team", "player"):
        raise HTTPException(status_code=400, detail="subject_type must be 'team' or 'player'")

    if subject_type == "team":
        subject = db.query(Team).filter(Team.id == subject_id, Team.game_id == game_id).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Team not found in this game")
        description = f"Will {subject.name} score over or under {threshold} total points?"
        market = BetMarket(
            game_id=game_id, race_id=None,
            market_type="over_under",
            description=description,
            status="open",
            threshold=threshold,
            subject_team_id=subject_id,
        )
    else:
        subject = db.query(Player).filter(Player.id == subject_id).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Player not found")
        description = f"Will {subject.name} score over or under {threshold} total points?"
        market = BetMarket(
            game_id=game_id, race_id=None,
            market_type="over_under",
            description=description,
            status="open",
            threshold=threshold,
            subject_player_id=subject_id,
        )

    db.add(market)
    db.flush()
    db.add(BetOption(market_id=market.id, label="Over"))
    db.add(BetOption(market_id=market.id, label="Under"))
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

    # Only collect placements for players who actually raced (blank = did not race)
    placements: dict = {}  # player_id -> placement (1–4)
    for player in players_in_game:
        key = f"player_{player.id}"
        val = form.get(key, "").strip()
        if not val:
            continue  # did not race
        try:
            placement = int(val)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid placement for {player.name}")
        if placement < 1 or placement > 4:
            raise HTTPException(status_code=400, detail=f"Placement must be 1–4 (got {placement})")
        placements[player.id] = placement

    if len(placements) != 4:
        raise HTTPException(status_code=400, detail=f"Exactly 4 players must race (got {len(placements)})")
    if len(set(placements.values())) != 4:
        raise HTTPException(status_code=400, detail="Each placement (1–4) must be unique")

    # Save results with points
    for player_id, placement in placements.items():
        pts = POINTS_MAP.get(placement, 0)
        result = RaceResult(race_id=race_id, player_id=player_id, placement=placement, points=pts)
        db.add(result)
        # Update total_races for each racer
        player_obj = next((p for p in players_in_game if p.id == player_id), None)
        if player_obj:
            player_obj.total_races += 1

    # Settle race markets
    winner_player_id = min(placements, key=lambda pid: placements[pid])
    winner_player = next(p for p in players_in_game if p.id == winner_player_id)

    for market in race.bet_markets:
        if market.market_type == "race_winner":
            winning_label = winner_player.name
            market.winning_outcome = winning_label
            market.status = "settled"
            _settle_market(db, market, winning_label)

    race.status = "completed"
    db.commit()
    return RedirectResponse(f"/admin/games/{game_id}", status_code=302)


def _get_team_points(game_id: int, db: Session) -> dict:
    """Return {team_id: total_points} for all teams in a game."""
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        return {}
    result = {}
    for team in game.teams:
        pts = 0
        for player_id in (team.player1_id, team.player2_id):
            rows = (
                db.query(RaceResult)
                .join(Race, RaceResult.race_id == Race.id)
                .filter(Race.game_id == game_id, RaceResult.player_id == player_id)
                .all()
            )
            pts += sum(r.points or 0 for r in rows)
        result[team.id] = pts
    return result


def _get_player_points(game_id: int, db: Session) -> dict:
    """Return {player_id: total_points} for all players in a game."""
    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        return {}
    result = {}
    for team in game.teams:
        for player_id in (team.player1_id, team.player2_id):
            rows = (
                db.query(RaceResult)
                .join(Race, RaceResult.race_id == Race.id)
                .filter(Race.game_id == game_id, RaceResult.player_id == player_id)
                .all()
            )
            result[player_id] = sum(r.points or 0 for r in rows)
    return result


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

def _parse_firebase_export(data: dict) -> list:
    """Convert a Firebase RTDB export to the player-list format expected by the importer.

    Extracts ELO values from data["ELO"] and computes total_games, total_races,
    and total_wins per player from data["act-history"].
    """
    # Step 1: compute stats from act-history
    stats: dict = {}  # {stripped_name: {games, races, wins}}
    for game in data.get("act-history", {}).values():
        if not isinstance(game, dict) or "teams" not in game or "races" not in game:
            continue
        teams = game["teams"]
        totals = game.get("totals", {})
        if totals:
            min_score = min(totals.values())
            winning_teams = {t for t, s in totals.items() if s == min_score}
        else:
            winning_teams = set()

        # Map (team_letter, slot) -> stripped player name
        slot_map: dict = {}
        for team_letter, team_data in teams.items():
            if not isinstance(team_data, dict):
                continue
            for slot, name in team_data.get("players", {}).items():
                if not name:
                    continue
                sname = name.strip()
                slot_map[(team_letter, slot)] = sname
                if sname not in stats:
                    stats[sname] = {"games": 0, "races": 0, "wins": 0}
                stats[sname]["games"] += 1
                if team_letter in winning_teams:
                    stats[sname]["wins"] += 1

        # Count individual race appearances
        for race_list in game.get("races", {}).values():
            for race_entry in race_list:
                if not isinstance(race_entry, dict):
                    continue
                for team_letter, result in race_entry.items():
                    if not isinstance(result, dict):
                        continue
                    racer_slot = result.get("racer")
                    sname = slot_map.get((team_letter, racer_slot))
                    if sname:
                        stats[sname]["races"] += 1

    # Step 2: build player list from ELO dict
    result = []
    for name, elo in data["ELO"].items():
        s = stats.get(name.strip(), {"games": 0, "races": 0, "wins": 0})
        result.append({
            "name": name,
            "elo": float(elo),
            "total_wins": s["wins"],
            "total_games": s["games"],
            "total_races": s["races"],
        })
    return result



@router.get("/elo-import", response_class=HTMLResponse)
def elo_import_page(request: Request, reset: str = None, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    message = "All ELO data has been reset to defaults." if reset == "1" else None
    return templates.TemplateResponse(
        "admin/elo_import.html",
        {"request": request, "user": admin, "message": message, "reset_success": reset == "1"},
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

    if isinstance(data, dict) and "ELO" in data:
        data = _parse_firebase_export(data)
    elif not isinstance(data, list):
        return templates.TemplateResponse(
            "admin/elo_import.html",
            {"request": request, "user": get_current_user(request, db), "message": "JSON must be a list of player objects or a Firebase RTDB export"},
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


@router.post("/elo/reset")
def elo_reset(request: Request, db: Session = Depends(get_db)):
    """Reset all player ELO stats to defaults and clear head-to-head records."""
    require_admin(request, db)
    db.query(Player).update({
        "elo": 1000.0,
        "total_wins": 0,
        "shirt_swap_count": 0,
        "total_games": 0,
        "total_races": 0,
    }, synchronize_session=False)
    db.query(HeadToHead).delete()
    db.commit()
    return RedirectResponse("/admin/elo-import?reset=1", status_code=302)


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
