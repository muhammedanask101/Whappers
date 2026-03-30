from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from fastapi import Cookie, Header, HTTPException, Response, status

from app.core.settings import get_settings
from app.db.mongo import get_collection, parse_datetime, to_iso, utc_now


ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 30
REFRESH_COOKIE_NAME = "wa_refresh_token"
ROLE_SUPERADMIN = "superadmin"
ROLE_BUSINESS_ADMIN = "business_admin"
ROLE_VENDOR = "vendor"
ALLOWED_ROLES = {ROLE_SUPERADMIN, ROLE_BUSINESS_ADMIN, ROLE_VENDOR}

PLAN_FEATURES = {
    "basic": {"orders", "catalog", "settings", "onboarding", "subscription"},
    "pro": {"orders", "catalog", "settings", "onboarding", "subscription", "analytics", "performance"},
    "enterprise": {
        "orders",
        "catalog",
        "settings",
        "onboarding",
        "subscription",
        "analytics",
        "performance",
        "vendors",
        "activity_logs",
    },
}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _jwt_secret() -> str:
    settings = get_settings()
    return os.getenv("JWT_SECRET", "").strip() or settings.session_secret


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash.startswith("$2"):
        return False
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def _encode_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    signature = hmac.new(_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_segment}.{payload_segment}.{_b64url_encode(signature)}"


def _decode_jwt(token: str) -> dict[str, Any]:
    try:
        header_segment, payload_segment, signature_segment = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token.") from exc
    signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
    expected = hmac.new(_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual = _b64url_decode(signature_segment)
    if not hmac.compare_digest(expected, actual):
        raise HTTPException(status_code=401, detail="Invalid token signature.")
    payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    exp = int(payload.get("exp") or 0)
    if exp <= int(datetime.now(timezone.utc).timestamp()):
        raise HTTPException(status_code=401, detail="Token expired.")
    return payload


def seed_auth_entities() -> None:
    users = get_collection("users")
    users.create_index("email", unique=True)
    users.create_index([("role", 1), ("business_id", 1)])
    tokens = get_collection("refresh_tokens")
    tokens.create_index("token_id", unique=True)
    tokens.create_index("user_id")
    tokens.create_index("expires_at")
    subscriptions = get_collection("subscriptions")
    subscriptions.create_index("business_id", unique=True)
    onboarding = get_collection("onboarding_states")
    onboarding.create_index("business_id", unique=True)

    admin_email = (
        os.getenv("SUPERADMIN_EMAIL", "").strip().lower()
        or os.getenv("MANAGER_EMAIL", "").strip().lower()
    )
    admin_password = os.getenv("SUPERADMIN_PASSWORD", "").strip() or os.getenv("MANAGER_PASSWORD", "").strip()
    if admin_email and admin_password:
        now = utc_now()
        existing = users.find_one({"email": admin_email})
        password_hash = hash_password(admin_password)
        if existing and str(existing.get("password_hash") or "").startswith("$2"):
            password_hash = str(existing["password_hash"])
        users.update_one(
            {"email": admin_email},
            {
                "$set": {
                    "email": admin_email,
                    "name": "Platform Superadmin",
                    "role": ROLE_SUPERADMIN,
                    "business_id": None,
                    "active": True,
                    "password_hash": password_hash,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )


def _serialize_user(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(document.get("user_id") or document.get("_id")),
        "email": document.get("email"),
        "name": document.get("name") or "",
        "role": document.get("role"),
        "business_id": document.get("business_id"),
        "active": bool(document.get("active", True)),
        "created_at": to_iso(parse_datetime(document.get("created_at"))),
        "updated_at": to_iso(parse_datetime(document.get("updated_at"))),
    }


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    if not email.strip():
        return None
    document = get_collection("users").find_one({"email": email.strip().lower()})
    return _serialize_user(document) if document else None


def _get_user_document(email: str) -> Optional[dict[str, Any]]:
    if not email.strip():
        return None
    return get_collection("users").find_one({"email": email.strip().lower(), "active": True})


def create_user(*, email: str, password: str, role: str, name: str, business_id: Optional[str] = None) -> dict[str, Any]:
    normalized_role = role.strip().lower()
    if normalized_role not in ALLOWED_ROLES:
        raise ValueError("Unsupported role.")
    normalized_email = email.strip().lower()
    if not normalized_email or not password.strip():
        raise ValueError("Email and password are required.")
    now = utc_now()
    document = {
        "user_id": f"user_{secrets.token_hex(8)}",
        "email": normalized_email,
        "name": name.strip() or normalized_email.split("@")[0],
        "password_hash": hash_password(password),
        "role": normalized_role,
        "business_id": business_id,
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    get_collection("users").insert_one(document)
    return _serialize_user(document)


def authenticate_credentials(email: str, password: str) -> Optional[dict[str, Any]]:
    seed_auth_entities()
    user = _get_user_document(email)
    if not user:
        return None
    if not verify_password(password, str(user.get("password_hash") or "")):
        return None
    return _serialize_user(user)


def _build_access_token(user: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["user_id"],
        "email": user["email"],
        "role": user["role"],
        "business_id": user.get("business_id"),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TOKEN_MINUTES)).timestamp()),
    }
    return _encode_jwt(payload)


def _build_refresh_token(user: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    token_id = secrets.token_hex(16)
    payload = {
        "sub": user["user_id"],
        "email": user["email"],
        "role": user["role"],
        "business_id": user.get("business_id"),
        "type": "refresh",
        "jti": token_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=REFRESH_TOKEN_DAYS)).timestamp()),
    }
    token = _encode_jwt(payload)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    record = {
        "token_id": token_id,
        "user_id": user["user_id"],
        "email": user["email"],
        "role": user["role"],
        "business_id": user.get("business_id"),
        "token_hash": token_hash,
        "created_at": now,
        "expires_at": now + timedelta(days=REFRESH_TOKEN_DAYS),
        "revoked_at": None,
    }
    return token, record


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.session_https_only,
        samesite=settings.session_same_site,
        max_age=REFRESH_TOKEN_DAYS * 24 * 60 * 60,
        domain=settings.session_cookie_domain,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path="/",
        domain=settings.session_cookie_domain,
        secure=settings.session_https_only,
        httponly=True,
        samesite=settings.session_same_site,
    )


def issue_auth_tokens(response: Response, user: dict[str, Any]) -> dict[str, Any]:
    access_token = _build_access_token(user)
    refresh_token, refresh_record = _build_refresh_token(user)
    get_collection("refresh_tokens").insert_one(refresh_record)
    _set_refresh_cookie(response, refresh_token)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
        "user": user,
    }


def refresh_auth_tokens(response: Response, refresh_token: str) -> dict[str, Any]:
    payload = _decode_jwt(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token.")
    token_id = str(payload.get("jti") or "")
    if not token_id:
        raise HTTPException(status_code=401, detail="Invalid refresh token.")
    record = get_collection("refresh_tokens").find_one({"token_id": token_id})
    if not record:
        raise HTTPException(status_code=401, detail="Refresh token not found.")
    if record.get("revoked_at") is not None:
        raise HTTPException(status_code=401, detail="Refresh token revoked.")
    expires_at = parse_datetime(record.get("expires_at"))
    if not expires_at or expires_at <= utc_now():
        raise HTTPException(status_code=401, detail="Refresh token expired.")
    provided_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(str(record.get("token_hash") or ""), provided_hash):
        raise HTTPException(status_code=401, detail="Refresh token mismatch.")
    user_doc = get_collection("users").find_one({"email": payload.get("email"), "active": True})
    if not user_doc:
        raise HTTPException(status_code=401, detail="User not found.")
    get_collection("refresh_tokens").update_one({"token_id": token_id}, {"$set": {"revoked_at": utc_now()}})
    return issue_auth_tokens(response, _serialize_user(user_doc))


def revoke_refresh_token(response: Response, refresh_token: Optional[str]) -> None:
    if refresh_token:
        try:
            payload = _decode_jwt(refresh_token)
            token_id = str(payload.get("jti") or "")
            if token_id:
                get_collection("refresh_tokens").update_one({"token_id": token_id}, {"$set": {"revoked_at": utc_now()}})
        except HTTPException:
            pass
    _clear_refresh_cookie(response)


def get_current_user(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    seed_auth_entities()
    bearer = str(authorization or "").strip()
    if bearer.lower().startswith("bearer "):
        payload = _decode_jwt(bearer.split(" ", 1)[1].strip())
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid access token.")
        return {
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role"),
            "business_id": payload.get("business_id"),
        }
    raise HTTPException(status_code=401, detail="Authentication required.")


def get_current_api_user(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    bearer = str(authorization or "").strip()
    if not bearer.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    payload = _decode_jwt(bearer.split(" ", 1)[1].strip())
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token.")
    return {
        "user_id": payload.get("sub"),
        "email": payload.get("email"),
        "role": payload.get("role"),
        "business_id": payload.get("business_id"),
    }


def require_roles(user: dict[str, Any], *roles: str) -> dict[str, Any]:
    if user.get("role") not in set(roles):
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    return user


def ensure_business_scope(user: dict[str, Any], business_id: Optional[str]) -> str:
    target_business_id = str(business_id or user.get("business_id") or "").strip()
    if user.get("role") == ROLE_SUPERADMIN:
        if not target_business_id:
            raise HTTPException(status_code=400, detail="business_id is required.")
        return target_business_id
    user_business_id = str(user.get("business_id") or "").strip()
    if not user_business_id:
        raise HTTPException(status_code=403, detail="No business scope assigned.")
    if target_business_id and target_business_id != user_business_id:
        raise HTTPException(status_code=403, detail="Cross-tenant access denied.")
    return user_business_id


def get_subscription_for_business(business_id: str) -> dict[str, Any]:
    subscription = get_collection("subscriptions").find_one({"business_id": business_id})
    if subscription:
        return {
            "business_id": business_id,
            "plan": subscription.get("plan", "basic"),
            "status": subscription.get("status", "inactive"),
            "features": sorted(PLAN_FEATURES.get(subscription.get("plan", "basic"), PLAN_FEATURES["basic"])),
            "current_period_end": to_iso(parse_datetime(subscription.get("current_period_end"))),
            "updated_at": to_iso(parse_datetime(subscription.get("updated_at"))),
        }
    return {
        "business_id": business_id,
        "plan": "basic",
        "status": "inactive",
        "features": sorted(PLAN_FEATURES["basic"]),
        "current_period_end": None,
        "updated_at": None,
    }


def require_feature_access(user: dict[str, Any], feature: str) -> None:
    if user.get("role") == ROLE_SUPERADMIN:
        return
    business_id = ensure_business_scope(user, user.get("business_id"))
    subscription = get_subscription_for_business(business_id)
    if feature not in set(subscription.get("features") or []):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Current subscription plan does not include '{feature}'.",
        )


def refresh_token_from_cookie(refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME)) -> Optional[str]:
    return refresh_token
