import os
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware

from app.db.database import init_db
from app.db.mongo import get_mongo_database
from app.routes.admin import router as admin_router
from app.routes.admin_api import router as admin_api_router
from app.routes.onboarding import router as onboarding_router
from app.routes.whatsapp import router as whatsapp_router
from app.services.admin import ensure_manager_seed
from app.services.intelligence.automation import run_automation_cycle


def load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()
init_db()

app = FastAPI(
    title="WhatsApp Conversational Commerce Agent",
    version="2.0.0",
    description="Agentic conversational commerce backend for WhatsApp.",
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("MANAGER_SESSION_SECRET", "change-this-manager-secret"),
    same_site="lax",
    https_only=False,
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please slow down."})


app.include_router(onboarding_router)
app.include_router(whatsapp_router)
app.include_router(admin_router)
app.include_router(admin_api_router)


@app.on_event("startup")
def startup_checks() -> None:
    app.state.mongo_db = get_mongo_database()
    ensure_manager_seed()
    if not getattr(app.state, "automation_thread_started", False):
        thread = threading.Thread(target=run_automation_cycle, daemon=True)
        thread.start()
        app.state.automation_thread_started = True


@app.get("/health")
def healthcheck() -> dict:
    return {"status": "ok"}
