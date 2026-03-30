from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Dict

from app.core.settings import get_settings
from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, parse_datetime, utc_now
from app.services.agent.learning import record_event
from app.services.messaging.meta_provider import MetaProviderError
from app.services.messaging.utils import normalize_phone


TERMINAL_MESSAGE_STATUSES = {"failed", "template_required", "rate_limited", "sent", "delivered", "read"}
DELIVERY_STATUSES = {"sent", "delivered", "read"}


def track_last_user_interaction(business_id: str, phone: str) -> None:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return
    now = utc_now()
    get_collection("conversation_state").update_one(
        {"business_id": business_id or DEFAULT_BUSINESS_ID, "phone": normalized_phone},
        {"$set": {"last_inbound_at": now, "last_user_interaction_at": now, "updated_at": now}},
        upsert=True,
    )


def reserve_outbound_message(
    *,
    business_id: str,
    to: str,
    from_number: str,
    body: str,
    notification_type: str,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    now = utc_now()
    normalized_to = normalize_phone(to)
    normalized_from = normalize_phone(from_number)
    idempotency_key = str((metadata or {}).get("idempotency_key") or _build_idempotency_key(business_id, normalized_to, body, notification_type, metadata or {}))
    collection = get_collection("message_dispatch")
    existing = collection.find_one({"idempotency_key": idempotency_key})
    if existing and str(existing.get("status") or "") in TERMINAL_MESSAGE_STATUSES:
        return {"duplicate": True, "document": existing}
    if existing:
        collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {"updated_at": now, "status": "pending"}},
        )
        existing["status"] = "pending"
        existing["updated_at"] = now
        return {"duplicate": False, "document": existing}
    document = {
        "business_id": business_id or DEFAULT_BUSINESS_ID,
        "to": normalized_to,
        "from": normalized_from,
        "body": body,
        "notification_type": notification_type,
        "metadata": metadata or {},
        "idempotency_key": idempotency_key,
        "status": "pending",
        "provider": "meta",
        "attempts": 0,
        "last_error": "",
        "message_id": "",
        "delivery_history": [],
        "created_at": now,
        "updated_at": now,
    }
    collection.insert_one(document)
    return {"duplicate": False, "document": collection.find_one({"idempotency_key": idempotency_key}) or document}


def enforce_messaging_policy(*, business_id: str, to: str, notification_type: str, dispatch_id: Any) -> Dict[str, Any]:
    normalized_to = normalize_phone(to)
    if _rate_limited(business_id):
        _update_dispatch(dispatch_id, status="rate_limited", last_error="Per-tenant message rate limit exceeded.")
        return {"allowed": False, "reason": "rate_limited"}
    if not _within_session_window(business_id, normalized_to):
        _update_dispatch(dispatch_id, status="template_required", last_error="WhatsApp template required outside the 24-hour session window.")
        record_event("message_template_required", business_id=business_id, phone=normalized_to, payload={"notification_type": notification_type})
        return {"allowed": False, "reason": "template_required"}
    return {"allowed": True}


def mark_outbound_sent(dispatch_id: Any, result: Dict[str, Any]) -> None:
    message_id = str(result.get("message_id") or "").strip()
    now = utc_now()
    update_fields = {
        "status": "sent",
        "message_id": message_id,
        "provider_response": result,
        "updated_at": now,
    }
    get_collection("message_dispatch").update_one(
        {"_id": dispatch_id},
        {
            "$set": update_fields,
            "$inc": {"attempts": 1},
            "$push": {"delivery_history": {"status": "sent", "at": now, "message_id": message_id}},
        },
    )


def mark_outbound_failure(dispatch_id: Any, exc: Exception) -> Dict[str, Any]:
    retryable = classify_retryable_error(exc)
    collection = get_collection("message_dispatch")
    current = collection.find_one({"_id": dispatch_id}) or {}
    attempts = int(current.get("attempts") or 0) + 1
    max_attempts = max(1, get_settings().messaging_retry_max_attempts)
    next_attempt_at = utc_now() + timedelta(seconds=_backoff_seconds(attempts))
    status = "queued" if retryable and attempts < max_attempts else "failed"
    update_fields: Dict[str, Any] = {
        "status": status,
        "last_error": str(exc),
        "updated_at": utc_now(),
    }
    if status == "queued":
        update_fields["next_attempt_at"] = next_attempt_at
    collection.update_one(
        {"_id": dispatch_id},
        {"$set": update_fields, "$inc": {"attempts": 1}},
    )
    return {"retryable": retryable and attempts < max_attempts, "next_attempt_at": next_attempt_at if status == "queued" else None}


def persist_delivery_status(event: Dict[str, Any], business_id: str) -> None:
    message_id = str(event.get("message_id") or "").strip()
    status = str(event.get("status") or "").strip()
    if not message_id or status not in DELIVERY_STATUSES:
        return
    timestamp = parse_datetime(event.get("timestamp")) or utc_now()
    get_collection("message_dispatch").update_one(
        {"business_id": business_id, "message_id": message_id},
        {
            "$set": {
                "status": status,
                "delivery_status": status,
                "delivery_updated_at": timestamp,
                "updated_at": utc_now(),
            },
            "$push": {"delivery_history": {"status": status, "at": timestamp, "raw": event}},
        },
    )


def retryable_dispatches(business_id: str) -> list[Dict[str, Any]]:
    now = utc_now()
    return list(
        get_collection("message_dispatch")
        .find(
            {
                "business_id": business_id,
                "status": "queued",
                "next_attempt_at": {"$lte": now},
            }
        )
        .sort("created_at", 1)
        .limit(50)
    )


def classify_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, MetaProviderError):
        return bool(exc.retryable)
    text = str(exc).lower()
    return any(token in text for token in {"timeout", "temporarily", "connection reset", "429", "500", "502", "503", "504", "network"})


def _within_session_window(business_id: str, phone: str) -> bool:
    state = get_collection("conversation_state").find_one({"business_id": business_id or DEFAULT_BUSINESS_ID, "phone": phone}) or {}
    last_touch = parse_datetime(state.get("last_user_interaction_at") or state.get("last_inbound_at"))
    if last_touch is None:
        return False
    return utc_now() - last_touch <= timedelta(hours=24)


def _rate_limited(business_id: str) -> bool:
    profile = get_collection("businesses").find_one({"business_id": business_id or DEFAULT_BUSINESS_ID}) or {}
    configured_limit = profile.get("messaging_rate_limit_per_minute")
    limit = int(configured_limit or get_settings().messaging_rate_limit_per_minute)
    window_start = utc_now() - timedelta(minutes=1)
    sent_in_window = get_collection("message_dispatch").count_documents(
        {
            "business_id": business_id or DEFAULT_BUSINESS_ID,
            "status": {"$in": ["pending", "sent", "delivered", "read"]},
            "created_at": {"$gte": window_start},
        }
    )
    return sent_in_window >= max(1, limit)


def _update_dispatch(dispatch_id: Any, **fields: Any) -> None:
    get_collection("message_dispatch").update_one({"_id": dispatch_id}, {"$set": fields | {"updated_at": utc_now()}})


def _build_idempotency_key(
    business_id: str,
    to: str,
    body: str,
    notification_type: str,
    metadata: Dict[str, Any],
) -> str:
    stable = {
        "business_id": business_id or DEFAULT_BUSINESS_ID,
        "to": to,
        "body": body.strip(),
        "notification_type": notification_type.strip(),
        "order_id": metadata.get("order_id"),
        "ticket_id": metadata.get("ticket_id"),
        "automation_key": metadata.get("automation_key"),
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _backoff_seconds(attempt: int) -> int:
    return min(30 * (2 ** max(0, attempt - 1)), 1800)
