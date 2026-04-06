import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

from database import Base, engine
from limiter import limiter
from routers import auth, games, players, bets, admin, transactions, p2p


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        proto = request.headers.get("x-forwarded-proto")
        if proto == "http":
            url = str(request.url).replace("http://", "https://", 1)
            return RedirectResponse(url, status_code=301)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup (idempotent)
    Base.metadata.create_all(bind=engine)
    # Add columns that may not exist in older deployments
    from sqlalchemy import text
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE players ADD COLUMN retired BOOLEAN NOT NULL DEFAULT FALSE"))
            conn.commit()
        except Exception:
            pass  # Column already exists
    # Seed SiteSettings singleton if missing
    from database import SessionLocal
    from models import SiteSettings
    with SessionLocal() as db:
        if not db.query(SiteSettings).first():
            db.add(SiteSettings())
            db.commit()
    yield


app = FastAPI(title="Mario Kart Prediction Market", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(HTTPSRedirectMiddleware)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(games.router)
app.include_router(players.router)
app.include_router(bets.router)
app.include_router(admin.router)
app.include_router(transactions.router)
app.include_router(p2p.router)
