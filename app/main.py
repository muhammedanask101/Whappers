import os
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.db.database import init_db
from app.db.mongo import get_mongo_database
from app.routes.admin_api import router as admin_api_router
from app.routes.admin import router as admin_router
from app.routes.whatsapp import router as whatsapp_router
from app.services.admin import ensure_manager_seed


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
    version="1.0.0",
    description="FastAPI backend for assisted conversational commerce on WhatsApp.",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("MANAGER_SESSION_SECRET", "change-this-manager-secret"),
    same_site="lax",
    https_only=False,
)
app.include_router(whatsapp_router)
app.include_router(admin_router)
app.include_router(admin_api_router)


@app.on_event("startup")
def startup_checks() -> None:
    app.state.mongo_db = get_mongo_database()
    ensure_manager_seed()


@app.get("/health")
def healthcheck() -> dict:
    return {"status": "ok"}
