from fastapi import APIRouter, Form, HTTPException, Response, status, Request
import time

from app.services.agent.controller import process_incoming_message
from app.services.llm import safe_user_facing_text
from app.services.vendor import (
    get_business_by_vendor_phone,
    get_business_by_whatsapp_number,
    normalize_phone,
    send_whatsapp_message,
)

from twilio.request_validator import RequestValidator
import os
from dotenv import load_dotenv

from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

limiter = Limiter(key_func=get_remote_address)

load_dotenv()

# 🔐 Secure token loading
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
if not TWILIO_AUTH_TOKEN:
    raise ValueError("TWILIO_AUTH_TOKEN not set in environment")

validator = RequestValidator(TWILIO_AUTH_TOKEN)

router = APIRouter(tags=["whatsapp"])
_RECENT_WEBHOOKS: dict[str, float] = {}


def _log_whatsapp(event: str, **details):
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[route.whatsapp] {event} {normalized}".rstrip())


# 🔐 Verify Twilio request
async def verify_request(request: Request):
    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()

    return validator.validate(
        str(request.url),
        dict(form),
        signature
    )


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
    _RECENT_WEBHOOKS[dedupe_key] = now
    return False


@router.post("/webhook/whatsapp")
@limiter.limit("10/minute")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
    MessageSid: str = Form(""),
) -> Response:
    _log_whatsapp("webhook_received", from_number=From, to_number=To, body_preview=Body[:240], env=os.getenv("ENV"))

    # 🔐 Security check (skip in dev if needed)
    if os.getenv("ENV") == "prod":
        if not await verify_request(request):
            raise HTTPException(status_code=403, detail="Unauthorized")

    if not Body or not From:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Twilio WhatsApp webhook fields."
        )

    sender = normalize_phone(From)
    recipient = normalize_phone(To)
    if _is_duplicate_webhook(MessageSid, sender, Body):
        _log_whatsapp("webhook_duplicate_ignored", sender=sender, message_sid=MessageSid, body_preview=Body[:120])
        return Response(content="", media_type="text/plain", status_code=200)

    business = _resolve_business(sender, recipient)
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active business matched this WhatsApp channel."
        )

    role = "vendor" if sender == business.get("vendor_phone") else "customer"
    _log_whatsapp("business_resolved", sender=sender, recipient=recipient, business_id=business.get("business_id"), role=role)

    reply = await process_incoming_message(
        Body,
        {
            "phone": sender,
            "role": role,
            "business_id": business["business_id"],
            "business_name": business["name"],
        },
    )
    reply = safe_user_facing_text(reply)[:1500]

    _log_whatsapp("reply_ready", sender=sender, role=role, reply_preview=reply[:240])

    send_whatsapp_message(
        sender,
        reply,
        from_number=business.get("whatsapp_number")
    )
    _log_whatsapp("webhook_complete", sender=sender, business_id=business.get("business_id"))

    return Response(content="", media_type="text/plain", status_code=200)
