from __future__ import annotations

from app.services.vendor import create_business as create_vendor_business


def create_business(name: str, vendor_phone: str) -> dict:
    return create_vendor_business(
        name=name,
        whatsapp_number=vendor_phone,
        vendor_phone=vendor_phone,
        business_type="retail",
        description="New WhatsApp commerce business.",
    )
