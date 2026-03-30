from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.settings import get_settings
from app.db.database import init_db
from app.db.mongo import get_mongo_database
from app.routes.admin_api import router as admin_api_router
from app.routes.api_auth import router as api_auth_router
from app.routes.api_dashboard import router as api_dashboard_router
from app.routes.api_subscription import router as api_subscription_router
from app.routes.onboarding import router as onboarding_router
from app.routes.whatsapp import router as whatsapp_router
from app.services.auth import seed_auth_entities
from app.services.intelligence.automation import AutomationScheduler


settings = get_settings()
init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mongo_db = get_mongo_database()
    app.state.settings = settings
    app.state.automation_scheduler = AutomationScheduler(interval_seconds=settings.automation_interval_seconds)
    seed_auth_entities()
    await app.state.automation_scheduler.start()
    try:
        yield
    finally:
        await app.state.automation_scheduler.stop()


app = FastAPI(
    title="WhatsApp Conversational Commerce Agent",
    version="3.0.0",
    description="Agentic conversational commerce backend for WhatsApp.",
    lifespan=lifespan,
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Too many requests. Please slow down."})


app.include_router(onboarding_router)
app.include_router(whatsapp_router)
app.include_router(admin_api_router)
app.include_router(api_auth_router)
app.include_router(api_dashboard_router)
app.include_router(api_subscription_router)


@app.get("/health")
def healthcheck() -> dict:
    return {"status": "ok", "environment": settings.environment, "debug_mode": settings.debug_mode}
