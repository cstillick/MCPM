import os
import re

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from auth import (
    create_access_token, hash_password, verify_password,
    get_current_user,
)
from database import get_db
from limiter import limiter
from models import PendingRegistration, User
from template_env import templates

USERNAME_RE = re.compile(r'^[a-zA-Z0-9]{3,32}$')

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
@limiter.limit("10/minute")
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password"},
            status_code=400,
        )
    token = create_access_token({"sub": user.username})
    resp = RedirectResponse("/", status_code=302)
    is_secure = os.getenv("SECURE_COOKIES", "false").lower() == "true"
    resp.set_cookie(
        "access_token",
        token,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    return resp


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request, "error": None, "success": False})


@router.post("/register", response_class=HTMLResponse)
@limiter.limit("5/minute")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    def error(msg):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": msg, "success": False},
            status_code=400,
        )

    if not USERNAME_RE.match(username):
        return error("Username must be 3–32 characters, letters and numbers only.")
    if len(password) < 6:
        return error("Password must be at least 6 characters.")
    if password != confirm_password:
        return error("Passwords do not match.")
    if db.query(User).filter(User.username == username).first():
        return error("That username is already taken.")
    if db.query(PendingRegistration).filter(PendingRegistration.username == username).first():
        return error("An account request for that username is already pending.")

    db.add(PendingRegistration(username=username, password_hash=hash_password(password)))
    db.commit()
    return templates.TemplateResponse("register.html", {"request": request, "error": None, "success": True})


@router.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("access_token")
    return resp
