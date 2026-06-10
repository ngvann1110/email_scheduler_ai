import asyncio
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import FileResponse

from app.core.config import settings
from app.db.sqlite import init_db
from app.core.gmail_poller import poll_gmail
from app.core.daily_digest import run_daily_digest
from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.webhook import router as webhook_router

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.DEBUG),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent  # app/

app = FastAPI(title="Email Scheduler AI")

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Required for OAuth CSRF state (request.session in auth.py)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.JWT_SECRET_KEY,
    same_site="lax",
    https_only=False,
)

init_db()
app.include_router(auth_router)
app.include_router(webhook_router)
app.include_router(chat_router)


@app.on_event("startup")
async def startup_event():
    logger.info("[Main] Server khởi động, bắt đầu Gmail Poller...")
    asyncio.create_task(poll_gmail())
    asyncio.create_task(run_daily_digest())


@app.get("/ui")
def ui():
    return FileResponse(BASE_DIR / "chat_ui.html")
