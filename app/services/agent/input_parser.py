from __future__ import annotations

import re
from typing import Any, Dict

from app.services.cart_engine import parse_cart_requests


_ADDRESS_KEYWORDS = {
    "road",
    "rd",
    "street",
    "st",
    "lane",
    "house",
    "building",
    "floor",
    "flat",
    "near",
    "pin",
    "city",
    "district",
    "post",
    "po",
    "p.o",
    "junction",
    "villa",
    "block",
    "nagar",
}
_NON_ADDRESS_RESPONSES = {
    "no",
    "wait",
    "i mean",
    "not that",
    "wrong",
    "change it",
    "hold on",
    "later",
    "okay",
    "ok",
    "yes",
}


def normalize_address(raw_input: str) -> Dict[str, Any]:
    raw = str(raw_input or "").strip()
    lowered = raw.lower()
    tokens = [token for token in re.split(r"\s+", raw) if token.strip()]
    comma_segments = [segment.strip() for segment in re.split(r",|\n", raw) if segment.strip()]
    has_number = any(any(ch.isdigit() for ch in token) for token in tokens)
    has_keyword = any(token.strip(".,").lower() in _ADDRESS_KEYWORDS for token in tokens)
    has_capitalized = any(token[:1].isupper() and len(token) > 1 for token in tokens)
    pincode_match = re.search(r"\b(\d{5,6})\b", raw)
    conversational = lowered in _NON_ADDRESS_RESPONSES or any(lowered.startswith(prefix) for prefix in {"no ", "wait ", "i mean ", "change "})

    if conversational:
        return {
            "address_line": "",
            "area": "",
            "pincode": "",
            "normalized": "",
            "is_valid": False,
            "reason": "conversational",
        }

    address_line = comma_segments[0] if comma_segments else " ".join(tokens[: min(4, len(tokens))]).strip()
    area = comma_segments[1] if len(comma_segments) > 1 else " ".join(tokens[4:]).strip()
    normalized = ", ".join(segment for segment in [address_line, area] if segment).strip(", ")
    if pincode_match and pincode_match.group(1) not in normalized:
        normalized = f"{normalized}, {pincode_match.group(1)}".strip(", ")

    is_valid = len(raw) > 10 and (has_number or "," in raw or has_keyword or has_capitalized)
    return {
        "address_line": address_line,
        "area": area,
        "pincode": pincode_match.group(1) if pincode_match else "",
        "normalized": normalized or raw,
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
