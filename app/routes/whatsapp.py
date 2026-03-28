from fastapi import APIRouter, Form, HTTPException, Response, status

from app.services.agent import process_incoming_message
from app.services.vendor import get_business_by_vendor_phone, get_business_by_whatsapp_number, normalize_phone, send_whatsapp_message

router = APIRouter(tags=["whatsapp"])


def _resolve_business(sender: str, recipient: str):
    business = get_business_by_whatsapp_number(recipient)
    if business:
        return business
    return get_business_by_vendor_phone(sender)


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(Body: str = Form(""), From: str = Form(""), To: str = Form("")) -> Response:
    if not Body or not From:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing Twilio WhatsApp webhook fields.")

    sender = normalize_phone(From)
    recipient = normalize_phone(To)
    business = _resolve_business(sender, recipient)
    if not business:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active business matched this WhatsApp channel.")

    role = "vendor" if sender == business.get("vendor_phone") else "customer"
    reply = process_incoming_message(
        Body,
        {"phone": sender, "role": role, "business_id": business["business_id"], "business_name": business["name"]},
    )
    send_whatsapp_message(sender, reply, from_number=business.get("whatsapp_number"))
    return Response(content="", media_type="text/plain", status_code=status.HTTP_200_OK)
