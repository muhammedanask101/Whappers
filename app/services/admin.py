from __future__ import annotations

import hmac
import os
import secrets
from typing import Any, Dict, List, Optional

import bcrypt

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now
from app.services.vendor import create_business, get_business_by_id, list_businesses, update_business


def ensure_manager_seed() -> None:
    email = os.getenv("MANAGER_EMAIL", "").strip().lower()
    password = os.getenv("MANAGER_PASSWORD", "").strip()
    if not email or not password:
        return
    existing = get_collection("manager_users").find_one({"email": email})
    if existing and str(existing.get("password_hash") or "").startswith("$2"):
        return
    now = utc_now()
    password_hash, password_salt = hash_password(password)
    get_collection("manager_users").update_one(
        {"email": email},
        {
            "$set": {
                "password_hash": password_hash,
                "password_salt": password_salt,
                "updated_at": now,
                "active": True,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


def hash_password(password: str) -> tuple[str, str]:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8"), salt.decode("utf-8")


def authenticate_manager(email: str, password: str) -> Optional[Dict[str, Any]]:
    ensure_manager_seed()
    document = get_collection("manager_users").find_one({"email": email.strip().lower(), "active": True})
    if not document:
        return None
    stored_hash = str(document.get("password_hash") or "")
    if not stored_hash.startswith("$2"):
        return None
    if not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
        return None
    return {"email": document["email"]}


def get_manager_overview() -> Dict[str, int]:
    return {
        "businesses": get_collection("businesses").count_documents({"active": True}),
        "products": get_collection("products").count_documents({"active": True}),
        "orders": get_collection("orders").count_documents({}),
        "pending_orders": get_collection("orders").count_documents({"status": "pending"}),
    }


def list_business_summaries() -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for business in list_businesses():
        business_id = business["business_id"]
        summaries.append(
            {
                **business,
                "product_count": get_collection("products").count_documents({"business_id": business_id, "active": True}),
                "order_count": get_collection("orders").count_documents({"business_id": business_id}),
                "pending_order_count": get_collection("orders").count_documents({"business_id": business_id, "status": "pending"}),
            }
        )
    return summaries


def get_business_snapshot(business_id: str) -> Optional[Dict[str, Any]]:
    business = get_business_by_id(business_id)
    if not business:
        return None
    business["recent_orders"] = list(get_collection("orders").find({"business_id": business_id}).sort("created_at", -1).limit(10))
    return business


def create_business_from_form(form: Dict[str, str]) -> Dict[str, Any]:
    return create_business(
        name=form.get("name", ""),
        slug=form.get("slug") or None,
        whatsapp_number=form.get("whatsapp_number", ""),
        vendor_phone=form.get("vendor_phone", ""),
        business_type=form.get("business_type", "retail"),
        description=form.get("description", ""),
        address=form.get("address", ""),
        hours=form.get("hours", ""),
        contact_phone=form.get("contact_phone", ""),
        support_details=form.get("support_details", ""),
        policies=form.get("policies", ""),
        delivery_info=form.get("delivery_info", ""),
    )


def update_business_from_form(business_id: str, form: Dict[str, str]) -> Optional[Dict[str, Any]]:
    return update_business(
        business_id,
        name=form.get("name"),
        slug=form.get("slug"),
        whatsapp_number=form.get("whatsapp_number"),
        vendor_phone=form.get("vendor_phone"),
        business_type=form.get("business_type"),
        description=form.get("description"),
        address=form.get("address"),
        hours=form.get("hours"),
        contact_phone=form.get("contact_phone"),
        support_details=form.get("support_details"),
        policies=form.get("policies"),
        delivery_info=form.get("delivery_info"),
        active=form.get("active") == "on",
    )


def delete_business(business_id: str) -> Optional[Dict[str, Any]]:
    business = get_business_by_id(business_id)
    if not business:
        return None
    active_count = get_collection("businesses").count_documents({"active": True})
    if business.get("active", True) and active_count <= 1:
        raise ValueError("At least one active business must remain.")
    return update_business(business_id, active=False)


def get_selected_business_id(requested: Optional[str]) -> str:
    if requested and get_business_by_id(requested):
        return requested
    default_business = get_business_by_id(DEFAULT_BUSINESS_ID)
    if default_business:
        return default_business["business_id"]
    first = get_collection("businesses").find_one(sort=[("created_at", 1)])
    return first["business_id"] if first else DEFAULT_BUSINESS_ID


def new_session_token() -> str:
    return secrets.token_urlsafe(24)


def ensure_csrf_token(session: Dict[str, Any]) -> str:
    token = str(session.get("csrf_token") or "")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token(session: Dict[str, Any], token: str) -> bool:
    expected = str(session.get("csrf_token") or "")
    return bool(expected and token) and hmac.compare_digest(expected, str(token))
