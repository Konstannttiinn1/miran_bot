"""Serve the private Telegram analytics Mini App; run on loopback behind HTTPS."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import re

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.bot import bot
from app.config import settings
from app.database.engine import init_db
from app.database.models import AdvertisingCampaign, User
from app.repositories import analytics_repo, db_repo
from app.services import analytics
from app.utils.telegram_auth import verify_init_data

WEB_DIR = Path(__file__).parent / "web_static"


@asynccontextmanager
async def lifespan(app):
    await init_db()
    yield
    await bot.session.close()


app = FastAPI(title="ORION Analytics", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def private_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; base-uri 'self'"
    )
    return response


@dataclass
class Viewer:
    telegram_id: int
    role: str
    lang: str
    name: str


async def viewer(authorization: str | None = Header(None)) -> Viewer:
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(401, "Open analytics from Telegram")
    try:
        signed_user = verify_init_data(authorization[4:], settings.bot_token, max_age=settings.analytics_init_data_max_age)
    except ValueError:
        raise HTTPException(401, "Invalid or expired Telegram session") from None
    tg = signed_user["id"]
    user = await db_repo.get_user_by_tg(tg)
    admin = tg in settings.admin_list
    if not admin and (user is None or user.role != "dealer" or user.is_blocked):
        raise HTTPException(403, "Analytics access denied")
    return Viewer(tg, "admin" if admin else "dealer", "ru" if admin else (user.lang or "fa"),
                  (user.username if user else None) or signed_user.get("first_name", str(tg)))


async def scope(who: Viewer, requested: int | None):
    if who.role == "dealer":
        if requested is not None and requested != who.telegram_id:
            raise HTTPException(403, "Foreign dealer scope")
        return who.telegram_id
    if requested is not None:
        dealer = await db_repo.get_user_by_tg(requested)
        if dealer is None or dealer.role != "dealer":
            raise HTTPException(400, "Unknown dealer")
    return requested


async def campaign_access(campaign_id, dealer):
    async with db_repo.async_session_factory() as session:
        campaign = await session.get(AdvertisingCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Unknown campaign")
    if dealer is not None and campaign.owner_telegram_id != dealer:
        raise HTTPException(403, "Foreign campaign")


async def public_bot_username():
    try:
        name = (await bot.me()).username
    except Exception:
        raise HTTPException(503, "Bot link temporarily unavailable") from None
    if not name or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", name):
        raise HTTPException(503, "Bot link unavailable")
    return name


@app.get("/api/me")
async def me(who: Viewer = Depends(viewer)):
    dealers = []
    if who.role == "admin":
        async with db_repo.async_session_factory() as session:
            rows = list(await session.scalars(select(User).where(User.role == "dealer").order_by(User.id)))
        dealers = [{"telegram_id": d.telegram_id, "name": d.username or str(d.telegram_id)} for d in rows]
    return {"viewer": vars(who), "dealers": dealers}


@app.get("/api/overview")
async def overview(period: str = "30d", dealer: int | None = Query(None, ge=1), who: Viewer = Depends(viewer)):
    dealer = await scope(who, dealer)
    try:
        return await analytics.overview(period, dealer)
    except ValueError:
        raise HTTPException(400, "Unknown period") from None


@app.get("/api/users")
async def users(paid: str = Query("all", pattern="^(all|paid|unpaid)$"),
                kind: str = Query("telegram", pattern="^(telegram|managed)$"),
                q: str = Query("", max_length=80), page: int = Query(0, ge=0, le=1000000),
                campaign: int | None = Query(None, ge=1), dealer: int | None = Query(None, ge=1),
                who: Viewer = Depends(viewer)):
    dealer = await scope(who, dealer)
    if campaign is not None:
        await campaign_access(campaign, dealer)
    return await analytics.users(dealer=dealer, paid=paid, kind=kind, q=q.strip(), page=page, campaign=campaign)


@app.get("/api/campaigns")
async def campaigns(dealer: int | None = Query(None, ge=1), who: Viewer = Depends(viewer)):
    dealer = await scope(who, dealer)
    name = await public_bot_username()
    items = await analytics.campaigns(dealer)
    for item in items:
        item["link"] = f"https://t.me/{name}?start={item['code']}"
    return {"items": items}


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@app.post("/api/campaigns", status_code=201)
async def create_campaign(body: CampaignCreate, dealer: int | None = Query(None, ge=1), who: Viewer = Depends(viewer)):
    owner = await scope(who, dealer) or who.telegram_id
    name = await public_bot_username()
    try:
        campaign = await analytics_repo.create_campaign(owner, body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"id": campaign.id, "name": campaign.name, "code": campaign.code,
            "link": f"https://t.me/{name}?start={campaign.code}", "owner_telegram_id": owner}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR, check_dir=False), name="assets")
