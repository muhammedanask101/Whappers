from __future__ import annotations

import re
from typing import Any, Dict

from app.services.cart_engine import parse_cart_requests


_NON_ADDRESS_RESPONSES = {"no", "wait", "i mean", "not that", "wrong", "change it", "hold on", "later", "okay", "ok", "yes"}


def normalize_address(raw_input: str) -> Dict[str, Any]:
    raw = str(raw_input or "").strip()
    lowered = raw.lower()
    if not raw or lowered in _NON_ADDRESS_RESPONSES:
        return {"address_line": "", "area": "", "landmark": "", "pincode": "", "normalized": "", "is_valid": False, "reason": "incomplete"}

    pincode_match = re.search(r"\b(\d{6})\b", raw)
    chunks = [segment.strip() for segment in re.split(r",|\n", raw) if segment.strip()]
    if len(chunks) < 2:
        return {"address_line": "", "area": "", "landmark": "", "pincode": "", "normalized": "", "is_valid": False, "reason": "incomplete"}

    address_line = chunks[0]
    area = chunks[1]
    landmark = chunks[2] if len(chunks) > 2 else ""
    pincode = pincode_match.group(1) if pincode_match else ""

    is_valid = bool(address_line and area and pincode)
    normalized = ", ".join(part for part in [address_line, area, landmark, pincode] if part)
    return {
        "address_line": address_line,
        "area": area,
        "landmark": landmark,
        "pincode": pincode,
        "normalized": normalized,
        "is_valid": is_valid,
        "reason": "" if is_valid else "incomplete",
    }


def parse_user_input(message: str) -> Dict[str, Any]:
    raw = str(message or "").strip()
    lowered = raw.lower()
    cart_requests = parse_cart_requests(raw)
    address = normalize_address(raw)
    intent = "general"
    if cart_requests:
        intent = "cart"
    elif address["is_valid"]:
        intent = "checkout"
    elif any(phrase in lowered for phrase in {"confirm", "place order", "checkout"}):
        intent = "checkout"
    elif any(phrase in lowered for phrase in {"show my cart", "view cart", "my cart"}):
        intent = "cart"
    first_request = cart_requests[0] if cart_requests else {}
    return {
        "intent": intent,
        "product": str(first_request.get("item") or "").strip(),
        "quantity": int(first_request.get("qty") or 1) if first_request else 1,
        "address": address,
        "operation": str(first_request.get("action") or "").strip(),
        "cart_requests": cart_requests,
    }
