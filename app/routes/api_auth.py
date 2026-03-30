from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.contracts import success_response
from app.services.auth import (
    ROLE_BUSINESS_ADMIN,
    authenticate_credentials,
    authenticate_legacy_admin_token,
    create_user,
    get_current_api_user,
    issue_auth_tokens,
    refresh_auth_tokens,
    refresh_token_from_cookie,
    revoke_refresh_token,
)
from app.services.dashboard import create_business_with_subscription, update_onboarding_state, upsert_subscription


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: Optional[str] = None
    password: str = ""
    admin_token: str = ""


class SignupRequest(BaseModel):
    owner_name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    business_name: str = Field(min_length=1)
    business_type: str = "retail"
    phone: str = ""
    whatsapp_number: str = ""
    plan: str = "basic"


@router.post("/login")
def login(payload: LoginRequest, response: Response) -> dict:
    user = None
    if payload.email and payload.password:
        user = authenticate_credentials(payload.email, payload.password)
    elif payload.admin_token:
        print("[route.api_auth] deprecated_admin_token_login_used")
        user = authenticate_legacy_admin_token(payload.admin_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    return success_response(issue_auth_tokens(response, user))


@router.post("/refresh")
def refresh(response: Response, refresh_token: Optional[str] = Depends(refresh_token_from_cookie)) -> dict:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token missing.")
    return success_response(refresh_auth_tokens(response, refresh_token))


@router.post("/logout")
def logout(response: Response, refresh_token: Optional[str] = Depends(refresh_token_from_cookie)) -> dict:
    revoke_refresh_token(response, refresh_token)
    return success_response({"logged_out": True})


@router.get("/me")
def me(user: dict = Depends(get_current_api_user)) -> dict:
    return success_response(user)


@router.post("/signup")
def signup(payload: SignupRequest, response: Response) -> dict:
    business = create_business_with_subscription(
        {
            "name": payload.business_name,
            "business_type": payload.business_type,
            "vendor_phone": payload.phone or payload.whatsapp_number,
            "contact_phone": payload.phone or payload.whatsapp_number,
            "whatsapp_number": payload.whatsapp_number or payload.phone,
            "plan": payload.plan,
            "subscription_status": "trialing",
        }
    )
    user = create_user(
        email=payload.email,
        password=payload.password,
        role=ROLE_BUSINESS_ADMIN,
        name=payload.owner_name,
        business_id=business["business_id"],
    )
    upsert_subscription(business["business_id"], plan=payload.plan, status="trialing")
    update_onboarding_state(business["business_id"], step="signup")
    return success_response({"business": business, **issue_auth_tokens(response, user)})
