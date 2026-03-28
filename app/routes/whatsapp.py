from fastapi import APIRouter, Form, HTTPException, Response, status, Request

from app.services.agent.controller import process_incoming_message
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


@router.post("/webhook/whatsapp")
@limiter.limit("10/minute")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    To: str = Form("")
) -> Response:

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

    business = _resolve_business(sender, recipient)
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active business matched this WhatsApp channel."
        )

    role = "vendor" if sender == business.get("vendor_phone") else "customer"

    reply = await process_incoming_message(
        Body,
        {
            "phone": sender,
            "role": role,
            "business_id": business["business_id"],
            "business_name": business["name"],
        },
    )

    send_whatsapp_message(
        sender,
        reply,
        from_number=business.get("whatsapp_number")
    )

    return Response(content="", media_type="text/plain", status_code=200)