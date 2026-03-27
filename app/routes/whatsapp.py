import os

from fastapi import APIRouter, Form, HTTPException, Response, status

from app.services.agent import process_incoming_message
from app.services.vendor import normalize_phone, send_whatsapp_message

router = APIRouter(tags=["whatsapp"])


def _vendor_phone() -> str:
    value = os.getenv("VENDOR_PHONE", "").strip()
    if not value:
        raise RuntimeError("VENDOR_PHONE is not configured.")
    return normalize_phone(value)


def _get_sender_role(sender: str) -> str:
    return "vendor" if normalize_phone(sender) == _vendor_phone() else "customer"


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(Body: str = Form(""), From: str = Form("")) -> Response:
    if not Body or not From:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Twilio WhatsApp webhook fields.",
        )

    sender = normalize_phone(From)
    role = _get_sender_role(sender)
    reply = process_incoming_message(Body, {"phone": sender, "role": role})
    send_whatsapp_message(sender, reply)
    return Response(content="", media_type="text/plain", status_code=status.HTTP_200_OK)
