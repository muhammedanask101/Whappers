from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.contracts import success_response
from app.services.auth import ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN, ensure_business_scope, get_current_user, get_subscription_for_business, require_roles
from app.services.dashboard import upsert_subscription


router = APIRouter(prefix="/api/subscription", tags=["subscription"])


class SubscriptionPayload(BaseModel):
    business_id: str = ""
    plan: str = "basic"
    status: str = "active"
    current_period_end: str = ""


@router.post("/create")
def create_subscription(payload: SubscriptionPayload, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN, ROLE_BUSINESS_ADMIN)
    business_id = payload.business_id or ensure_business_scope(user, payload.business_id or user.get("business_id"))
    return success_response(
        upsert_subscription(
            business_id=business_id,
            plan=payload.plan,
            status=payload.status,
            current_period_end=payload.current_period_end,
        )
    )


@router.get("/status")
def subscription_status(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN, ROLE_BUSINESS_ADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(get_subscription_for_business(business_id))
