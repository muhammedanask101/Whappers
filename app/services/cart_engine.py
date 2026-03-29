from __future__ import annotations

import json
import re
from typing import Any, Dict, List


_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:,| and | & )\s*", re.IGNORECASE)
_FILLER_WORDS = {
    "add",
    "give",
    "me",
    "please",
    "want",
    "i",
    "need",
    "another",
    "more",
    "to",
    "set",
    "cart",
    "remove",
    "delete",
    "take",
    "off",
    "put",
    "x",
}


def parse_cart_intent(message: str) -> str:
    lowered = str(message or "").strip().lower()
    if "clear cart" in lowered or lowered == "clear":
        return "clear"
    if lowered.startswith("remove") or lowered.startswith("delete"):
        return "remove"
    if lowered.startswith("set ") or " set " in lowered or " to " in lowered:
        return "set"
    quantity = extract_quantity(lowered)
    if quantity == 0:
        return "set"
    return "add"


def extract_quantity(message: str) -> int:
    lowered = str(message or "").strip().lower()
    set_match = re.search(r"\bto\s+(\d+)\b", lowered)
    if set_match:
        return max(0, int(set_match.group(1)))
    quantity_match = re.search(r"\b(\d+)\b", lowered)
    if quantity_match:
        return max(0, int(quantity_match.group(1)))
    if "another" in lowered:
        return 1
    return 1


def extract_item(message: str) -> str:
    lowered = str(message or "").strip().lower()
    if parse_cart_intent(lowered) == "clear":
        return ""
    cleaned = re.sub(r"\bto\s+\d+\b", " ", lowered)
    cleaned = re.sub(r"(?<=\d)x\b", " ", cleaned)
    cleaned = re.sub(r"\b\d+\b", " ", cleaned)
    cleaned = re.sub(r"\bx\b", " ", cleaned)
    cleaned = re.sub(r"[^\w\s.-]", " ", cleaned)
    tokens = [token for token in cleaned.split() if token and token not in _FILLER_WORDS]
    return " ".join(tokens).strip()


def apply_cart_action(cart: dict, item: str, action: str, qty: int) -> dict:
    current_items = list((cart or {}).get("items") or [])
    item_map = {str(row.get("name") or "").strip().lower(): max(0, int(row.get("qty") or 0)) for row in current_items if row.get("name")}

    if action == "clear":
        return {"items": [], "address": str((cart or {}).get("address") or "")}

    normalized_item = str(item or "").strip().lower()
    if not normalized_item:
        return {"items": [{"name": name, "qty": value} for name, value in item_map.items() if value > 0], "address": str((cart or {}).get("address") or "")}

    existing_qty = item_map.get(normalized_item, 0)
    if action == "remove":
        new_qty = existing_qty - max(0, qty)
    elif action == "set":
        new_qty = max(0, qty)
    else:
        new_qty = existing_qty + max(0, qty)

    if new_qty <= 0:
        item_map.pop(normalized_item, None)
    else:
        item_map[normalized_item] = new_qty

    return {
        "items": [{"name": name, "qty": value} for name, value in item_map.items() if value > 0],
        "address": str((cart or {}).get("address") or ""),
    }


def parse_cart_requests(message: str) -> List[Dict[str, Any]]:
    structured = _parse_structured_requests(message)
    if structured:
        return structured

    lowered = str(message or "").strip().lower()
    action = parse_cart_intent(lowered)
    if action == "clear":
        return [{"action": "clear", "item": "", "qty": 0, "explicit_qty": False}]

    requests: List[Dict[str, Any]] = []
    for segment in [part.strip() for part in _SEGMENT_SPLIT_RE.split(lowered) if part.strip()]:
        segment_action = parse_cart_intent(segment)
        if segment_action == "add" and action != "add":
            segment_action = action
        qty = extract_quantity(segment)
        item = extract_item(segment)
        explicit_qty = bool(re.search(r"\b\d+\b", segment)) or "another" in segment
        requests.append({"action": segment_action, "item": item, "qty": qty, "explicit_qty": explicit_qty})
    return requests


def _parse_structured_requests(message: str) -> List[Dict[str, Any]]:
    raw = str(message or "").strip()
    if not raw or raw[:1] not in {"{", "["}:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    top_level_action = parse_cart_intent(str(payload.get("action") or "")) if isinstance(payload, dict) else "add"
    items_payload: Any
    if isinstance(payload, dict):
        if top_level_action == "clear":
            return [{"action": "clear", "item": "", "qty": 0, "explicit_qty": False}]
        items_payload = payload.get("items")
        if items_payload is None and any(key in payload for key in {"product_name", "name", "product", "item"}):
            items_payload = [payload]
    elif isinstance(payload, list):
        items_payload = payload
    else:
        return []

    if not isinstance(items_payload, list):
        return []

    requests: List[Dict[str, Any]] = []
    for row in items_payload:
        if not isinstance(row, dict):
            continue
        item = str(row.get("product_name") or row.get("name") or row.get("product") or row.get("item") or "").strip().lower()
        if not item:
            continue
        raw_qty = row.get("quantity", row.get("qty", row.get("count", 1)))
        try:
            qty = max(0, int(raw_qty))
        except (TypeError, ValueError):
            qty = 1
        row_action = str(row.get("action") or "").strip().lower()
        action = row_action if row_action in {"add", "remove", "set", "clear"} else top_level_action
        if action not in {"add", "remove", "set", "clear"}:
            action = "add"
        requests.append({"action": action, "item": item, "qty": qty, "explicit_qty": True})
    return requests
