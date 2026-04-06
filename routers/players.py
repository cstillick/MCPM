from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import Player, HeadToHead
from template_env import templates

router = APIRouter()


@router.get("/players", response_class=HTMLResponse)
def players_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    players = (
        db.query(Player)
        .filter(Player.elo != 1000.0, Player.retired == False)
        .order_by(Player.elo.desc())
        .all()
    )

    # Build H2H matrix: {player_a_id: {player_b_id: {"wins": int, "losses": int}}}
    h2h_records = db.query(HeadToHead).all()
    h2h = {}
    for record in h2h_records:
        if record.player_a_id not in h2h:
            h2h[record.player_a_id] = {}
        if record.player_b_id not in h2h:
            h2h[record.player_b_id] = {}
        h2h[record.player_a_id][record.player_b_id] = {
            "wins": record.wins_a,
            "losses": record.wins_b,
        }
        h2h[record.player_b_id][record.player_a_id] = {
            "wins": record.wins_b,
            "losses": record.wins_a,
        }

    # Compute shirt swap %
    for p in players:
        if p.total_races > 0:
            p.shirt_swap_pct = round(p.shirt_swap_count / p.total_races * 100, 1)
        else:
            p.shirt_swap_pct = 0.0

    return templates.TemplateResponse(
        "players.html",
        {
            "request": request,
            "user": user,
            "players": players,
            "h2h": h2h,
        },
    )
