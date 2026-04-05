from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from auth import get_current_user, require_login
from database import get_db
from models import Bet, BetMarket, BetOption, User
from template_env import templates

router = APIRouter()


@router.get("/my-bets", response_class=HTMLResponse)
def my_bets(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    bets = (
        db.query(Bet)
        .filter(Bet.user_id == user.id)
        .order_by(Bet.created_at.desc())
        .all()
    )

    total_wagered = sum(b.coins_wagered for b in bets)
    settled = [b for b in bets if b.market.status == "settled"]
    won = [b for b in settled if b.payout and b.payout > 0]
    total_payout = sum(b.payout for b in settled if b.payout)

    return templates.TemplateResponse(
        "my_bets.html",
        {
            "request": request,
            "user": user,
            "bets": bets,
            "total_wagered": total_wagered,
            "total_payout": total_payout,
            "bets_won": len(won),
        },
    )


@router.post("/bets/place")
def place_bet(
    request: Request,
    market_id: int = Form(...),
    option_id: int = Form(...),
    coins_wagered: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    if coins_wagered <= 0:
        raise HTTPException(status_code=400, detail="Bet amount must be positive")

    market = db.query(BetMarket).filter(BetMarket.id == market_id).first()
    if not market or market.status != "open":
        raise HTTPException(status_code=400, detail="Market is not open for betting")

    option = db.query(BetOption).filter(
        BetOption.id == option_id, BetOption.market_id == market_id
    ).first()
    if not option:
        raise HTTPException(status_code=400, detail="Invalid option")

    # Check user hasn't already bet on this market
    existing = db.query(Bet).filter(
        Bet.user_id == user.id, Bet.market_id == market_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You have already placed a bet on this market")

    if user.coin_balance < coins_wagered:
        raise HTTPException(status_code=400, detail="Insufficient coins")

    # Deduct coins and record bet
    user.coin_balance -= coins_wagered
    option.total_coins_wagered += coins_wagered

    bet = Bet(
        user_id=user.id,
        market_id=market_id,
        option_id=option_id,
        coins_wagered=coins_wagered,
    )
    db.add(bet)
    db.commit()

    # Redirect back to the game or race page
    if market.race_id:
        race = market.race
        return RedirectResponse(
            f"/games/{market.game_id}/race/{race.race_number}",
            status_code=302,
        )
    return RedirectResponse(f"/games/{market.game_id}", status_code=302)
