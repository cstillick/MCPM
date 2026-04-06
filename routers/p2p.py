from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import (
    CoinTransaction, Game, P2PBet, P2PBetEntry, Race, SiteSettings, User,
)
from template_env import templates

router = APIRouter()


def _p2p_enabled(db: Session) -> bool:
    settings = db.query(SiteSettings).first()
    return bool(settings and settings.p2p_betting_enabled)


def _lock_coins(db: Session, user: User, p2p_bet: P2PBet, side: str, coins: int) -> P2PBetEntry:
    """Deduct coins from user, create entry + pending CoinTransaction."""
    user.coin_balance -= coins
    entry = P2PBetEntry(
        p2p_bet_id=p2p_bet.id,
        user_id=user.id,
        side=side,
        coins_locked=coins,
    )
    db.add(entry)
    db.flush()  # get entry.id

    txn = CoinTransaction(
        user_id=user.id,
        bet_id=None,
        type="pending",
        description=f"P2P — {p2p_bet.description} [{side.upper()}]",
        coins_wagered=coins,
        net_amount=-coins,
    )
    db.add(txn)
    db.flush()
    entry.coin_transaction_id = txn.id
    return entry


@router.post("/p2p/create")
def create_p2p_bet(
    request: Request,
    game_id: int = Form(...),
    market_type: str = Form(...),
    description: str = Form(...),
    race_id: int = Form(None),
    coins_wagered: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    if not _p2p_enabled(db):
        raise HTTPException(status_code=403, detail="P2P betting is not enabled")

    if coins_wagered < 1:
        raise HTTPException(status_code=400, detail="Minimum wager is 1 coin")

    if user.coin_balance < coins_wagered:
        raise HTTPException(status_code=400, detail="Insufficient coins")

    game = db.query(Game).filter(Game.id == game_id).first()
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if game.status == "completed":
        raise HTTPException(status_code=400, detail="Game is already completed")

    if market_type not in ("team_win", "race_winner", "free_form"):
        raise HTTPException(status_code=400, detail="Invalid market type")

    if market_type == "race_winner" and not race_id:
        raise HTTPException(status_code=400, detail="race_id required for race_winner bets")

    description = description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="Description is required")

    p2p_bet = P2PBet(
        game_id=game_id,
        race_id=race_id,
        creator_id=user.id,
        market_type=market_type,
        description=description,
        status="open",
    )
    db.add(p2p_bet)
    db.flush()

    _lock_coins(db, user, p2p_bet, "for", coins_wagered)
    db.commit()

    return RedirectResponse(f"/games/{game_id}", status_code=302)


@router.post("/p2p/{bet_id}/join")
def join_p2p_bet(
    bet_id: int,
    request: Request,
    side: str = Form(...),
    coins_wagered: int = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    if not _p2p_enabled(db):
        raise HTTPException(status_code=403, detail="P2P betting is not enabled")

    p2p_bet = db.query(P2PBet).filter(P2PBet.id == bet_id).first()
    if not p2p_bet:
        raise HTTPException(status_code=404, detail="P2P bet not found")
    if p2p_bet.status != "open":
        raise HTTPException(status_code=400, detail="This bet is no longer open")

    if side not in ("for", "against"):
        raise HTTPException(status_code=400, detail="Side must be 'for' or 'against'")

    if coins_wagered < 1:
        raise HTTPException(status_code=400, detail="Minimum wager is 1 coin")

    if user.coin_balance < coins_wagered:
        raise HTTPException(status_code=400, detail="Insufficient coins")

    # Prevent joining a side you're already on
    existing = db.query(P2PBetEntry).filter(
        P2PBetEntry.p2p_bet_id == bet_id,
        P2PBetEntry.user_id == user.id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You have already joined this bet")

    _lock_coins(db, user, p2p_bet, side, coins_wagered)
    db.commit()

    return RedirectResponse(f"/games/{p2p_bet.game_id}", status_code=302)


@router.post("/p2p/{bet_id}/cancel")
def cancel_p2p_bet(
    bet_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    p2p_bet = db.query(P2PBet).filter(P2PBet.id == bet_id).first()
    if not p2p_bet:
        raise HTTPException(status_code=404, detail="P2P bet not found")

    if p2p_bet.status not in ("open", "closed"):
        raise HTTPException(status_code=400, detail="Bet cannot be cancelled in its current state")

    if p2p_bet.creator_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only the proposer or an admin can cancel this bet")

    _do_cancel_p2p_bet(db, p2p_bet)
    db.commit()

    return RedirectResponse(f"/games/{p2p_bet.game_id}", status_code=302)


def _do_cancel_p2p_bet(db: Session, p2p_bet: P2PBet) -> None:
    """Refund all entries and mark bet as cancelled."""
    for entry in p2p_bet.entries:
        entry.payout = entry.coins_locked
        entry.user.coin_balance += entry.coins_locked
        if entry.coin_transaction_id:
            txn = db.get(CoinTransaction, entry.coin_transaction_id)
            if txn:
                txn.type = "refunded"
                txn.net_amount = 0
                txn.settled_at = datetime.utcnow()
    p2p_bet.status = "cancelled"
    p2p_bet.winning_side = "cancelled"
    p2p_bet.closed_at = datetime.utcnow()
    p2p_bet.settled_at = datetime.utcnow()
