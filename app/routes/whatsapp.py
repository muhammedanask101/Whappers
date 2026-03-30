from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.db.mongo import get_collection, utc_now
from app.services.agent.controller import process_incoming_message
from app.services.llm import safe_user_facing_text
from app.services.messaging.provider import get_messaging_provider
from app.services.messaging.service import persist_delivery_status, track_last_user_interaction
from app.services.vendor import get_business_by_vendor_phone, get_business_by_whatsapp_number, normalize_phone, send_whatsapp_message_async


router = APIRouter(tags=["whatsapp"])
limiter = Limiter(key_func=get_remote_address)
_RECENT_WEBHOOKS: dict[str, float] = {}


def _log_whatsapp(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[route.whatsapp] {event} {normalized}".rstrip())


async def verify_request(request: Request) -> bool:
    provider = get_messaging_provider()
    await provider.validate_webhook(request)
    return True


def _resolve_business(sender: str, recipient: str):
    business = get_business_by_whatsapp_number(recipient)
    if business:
        return business
    return get_business_by_vendor_phone(sender)


def _is_duplicate_webhook(message_sid: str, sender: str, body: str) -> bool:
    now = time.time()
    expired = [key for key, seen_at in _RECENT_WEBHOOKS.items() if now - seen_at > 180]
    for key in expired:
        _RECENT_WEBHOOKS.pop(key, None)
    dedupe_key = message_sid.strip() or f"{sender}|{body.strip().lower()}"
    if dedupe_key in _RECENT_WEBHOOKS:
        return True
    inserted = _reserve_distributed_dedupe_key(dedupe_key)
    if inserted:
        _RECENT_WEBHOOKS[dedupe_key] = now
        return False
    return True


def _reserve_distributed_dedupe_key(dedupe_key: str) -> bool:
    result = get_collection("webhook_dedupe").update_one(
        {"key": dedupe_key},
        {"$setOnInsert": {"key": dedupe_key, "created_at": utc_now()}},
        upsert=True,
    )
    return bool(result.upserted_id)


async def _read_incoming_payload(request: Request) -> Dict[str, Any]:
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Meta webhook payload must be JSON.")
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON webhook payload.")
    return payload


@router.get("/webhook/whatsapp")
async def whatsapp_webhook_verify(request: Request) -> Response:
    provider = get_messaging_provider()
    challenge = await provider.validate_webhook(request)
    return Response(content=challenge or "", media_type="text/plain", status_code=200)


@router.post("/webhook/whatsapp")
@limiter.limit("10/minute")
async def whatsapp_webhook(request: Request) -> Response:
    if not await verify_request(request):
        raise HTTPException(status_code=403, detail="Unauthorized")
    provider = get_messaging_provider()
    payload = await _read_incoming_payload(request)
    incoming = provider.parse_incoming(payload)
    sender = normalize_phone(str(incoming.get("from") or ""))
    recipient = normalize_phone(str(incoming.get("to") or ""))
    body = str(incoming.get("message") or "")
    message_type = str(incoming.get("type") or "text")
    message_id = str(incoming.get("message_id") or "")
    _log_whatsapp(
        "webhook_received",
        provider=getattr(provider, "provider_name", ""),
        from_number=sender,
        to_number=recipient,
        message_type=message_type,
        message_id=message_id,
        body_preview=body[:240],
    )
    if str(incoming.get("event_type") or "") == "status":
        business = _resolve_business(sender, recipient)
        if business:
            persist_delivery_status(dict(incoming.get("status_payload") or {}), str(business.get("business_id") or ""))
        _log_whatsapp("webhook_status_processed", message_id=message_id, status=incoming.get("status"))
        return Response(content="", media_type="text/plain", status_code=200)
    if message_type != "text" or not body or not sender:
        _log_whatsapp("webhook_ignored_non_text", provider=getattr(provider, "provider_name", ""), message_type=message_type, message_id=message_id)
        return Response(content="", media_type="text/plain", status_code=200)

    if _is_duplicate_webhook(message_id, sender, body):
        _log_whatsapp("webhook_duplicate_ignored", sender=sender, message_sid=message_id)
        return Response(content="", media_type="text/plain", status_code=200)

    business = _resolve_business(sender, recipient)
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active business matched this WhatsApp channel.")
    track_last_user_interaction(str(business.get("business_id") or ""), sender)

    role = "vendor" if sender == business.get("vendor_phone") else "customer"
    graceful_reply = "We received your message. Please try again shortly."
    try:
        reply = await process_incoming_message(
            body,
            {"phone": sender, "role": role, "business_id": business["business_id"], "business_name": business["name"]},
        )
        reply = safe_user_facing_text(reply)[:1500] or graceful_reply
    except Exception as exc:
        _log_whatsapp("agent_failed", sender=sender, business_id=business.get("business_id"), error=str(exc))
        reply = graceful_reply

    send_result = await send_whatsapp_message_async(
        sender,
        reply,
        from_number=business.get("whatsapp_number"),
        business_id=business.get("business_id"),
        notification_type="customer_reply" if role == "customer" else "vendor_reply",
    )
    _log_whatsapp("webhook_complete", sender=sender, business_id=business.get("business_id"), vendor_send_success=send_result.get("success"))
    return Response(content="", media_type="text/plain", status_code=200)
