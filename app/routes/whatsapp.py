from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from twilio.request_validator import RequestValidator

from app.core.settings import get_settings
from app.db.mongo import get_collection, utc_now
from app.services.agent.controller import process_incoming_message
from app.services.llm import safe_user_facing_text
from app.services.vendor import get_business_by_vendor_phone, get_business_by_whatsapp_number, normalize_phone, send_whatsapp_message_async


router = APIRouter(tags=["whatsapp"])
limiter = Limiter(key_func=get_remote_address)
_RECENT_WEBHOOKS: dict[str, float] = {}


def _log_whatsapp(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[route.whatsapp] {event} {normalized}".rstrip())


async def verify_request(request: Request) -> bool:
    settings = get_settings()
    if settings.debug_mode or settings.environment != "prod":
        return True
    if not settings.twilio_auth_token:
        return False
    validator = RequestValidator(settings.twilio_auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    return validator.validate(str(request.url), dict(form), signature)


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


@router.post("/webhook/whatsapp")
@limiter.limit("10/minute")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
    MessageSid: str = Form(""),
) -> Response:
    _log_whatsapp("webhook_received", from_number=From, to_number=To, body_preview=Body[:240])
    if not await verify_request(request):
        raise HTTPException(status_code=403, detail="Unauthorized")
    if not Body or not From:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Twilio WhatsApp webhook fields.")

    sender = normalize_phone(From)
    recipient = normalize_phone(To)
    if _is_duplicate_webhook(MessageSid, sender, Body):
        _log_whatsapp("webhook_duplicate_ignored", sender=sender, message_sid=MessageSid)
        return Response(content="", media_type="text/plain", status_code=200)

    business = _resolve_business(sender, recipient)
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active business matched this WhatsApp channel.")

    role = "vendor" if sender == business.get("vendor_phone") else "customer"
    graceful_reply = "We received your message. Please try again shortly."
    try:
        reply = await process_incoming_message(
            Body,
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
