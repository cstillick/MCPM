import csv
import io
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from database import get_db
from models import CoinTransaction, User
from template_env import templates

router = APIRouter()


def _build_query(db: Session, username: str = None, txn_type: str = None,
                 from_date: date = None, to_date: date = None, user_id: int = None):
    q = db.query(CoinTransaction).join(User, CoinTransaction.user_id == User.id)
    if user_id:
        q = q.filter(CoinTransaction.user_id == user_id)
    if username:
        q = q.filter(User.username.ilike(f"%{username}%"))
    if txn_type:
        q = q.filter(CoinTransaction.type == txn_type)
    if from_date:
        q = q.filter(CoinTransaction.created_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        q = q.filter(CoinTransaction.created_at <= datetime.combine(to_date, datetime.max.time()))
    return q.order_by(CoinTransaction.created_at.desc())


def _to_csv(transactions, include_username: bool) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    headers = []
    if include_username:
        headers.append("Username")
    headers += ["Type", "Description", "Coins Wagered", "Net Amount", "Date Placed", "Date Settled"]
    writer.writerow(headers)
    for txn in transactions:
        row = []
        if include_username:
            row.append(txn.user.username)
        row += [
            txn.type,
            txn.description,
            txn.coins_wagered if txn.coins_wagered is not None else "",
            txn.net_amount,
            txn.created_at.strftime("%Y-%m-%d %H:%M") if txn.created_at else "",
            txn.settled_at.strftime("%Y-%m-%d %H:%M") if txn.settled_at else "",
        ]
        writer.writerow(row)
    return output.getvalue()


# ── User routes ───────────────────────────────────────────────────────────────

@router.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request,
    txn_type: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    transactions = _build_query(db, txn_type=txn_type, from_date=from_date,
                                to_date=to_date, user_id=user.id).all()
    return templates.TemplateResponse("transactions.html", {
        "request": request,
        "user": user,
        "transactions": transactions,
        "filters": {"type": txn_type or "", "from": str(from_date or ""), "to": str(to_date or "")},
    })


@router.get("/transactions/export")
def transactions_export(
    request: Request,
    txn_type: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)

    transactions = _build_query(db, txn_type=txn_type, from_date=from_date,
                                to_date=to_date, user_id=user.id).all()
    content = _to_csv(transactions, include_username=False)
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=my_transactions.csv"},
    )


# ── Admin routes ──────────────────────────────────────────────────────────────

@router.get("/admin/transactions", response_class=HTMLResponse)
def admin_transactions_page(
    request: Request,
    username: Optional[str] = Query(None),
    txn_type: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    transactions = _build_query(db, username=username, txn_type=txn_type,
                                from_date=from_date, to_date=to_date).all()
    return templates.TemplateResponse("admin/transactions.html", {
        "request": request,
        "user": admin,
        "transactions": transactions,
        "filters": {
            "username": username or "",
            "type": txn_type or "",
            "from": str(from_date or ""),
            "to": str(to_date or ""),
        },
    })


@router.get("/admin/transactions/export")
def admin_transactions_export(
    request: Request,
    username: Optional[str] = Query(None),
    txn_type: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    require_admin(request, db)
    transactions = _build_query(db, username=username, txn_type=txn_type,
                                from_date=from_date, to_date=to_date).all()
    content = _to_csv(transactions, include_username=True)
    return StreamingResponse(
        io.StringIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )
