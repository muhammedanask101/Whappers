import os
from pathlib import Path

from fastapi import FastAPI

from app.db.database import init_db
from app.routes.whatsapp import router as whatsapp_router


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

app.include_router(whatsapp_router)


@app.get("/health")
def healthcheck() -> dict:
    return {"status": "ok"}

