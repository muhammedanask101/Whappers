from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable, Dict, List


LLMExtractor = Callable[[str], List[Dict[str, Any]]]


_TOKEN_RE = re.compile(r"\d+x|x\d+|\d+|[a-z][a-z0-9.\-]*", re.IGNORECASE)
_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:,|\band\b|&|\+)\s*", re.IGNORECASE)
_JOINERS = {"and", "&", "+", ",", "x"}
_LEAD_FILLERS = {"i", "want", "need", "add", "buy", "order", "please", "also", "me", "to", "cart"}
_TRAIL_FILLERS = {"please", "cart", "now"}


def extract_cart_candidates(message: str, *, llm_fallback: LLMExtractor | None = None) -> Dict[str, Any]:
    text = str(message or "").strip()
    result: Dict[str, Any] = {"items": [], "warnings": [], "used_llm": False, "sources": [], "ambiguous": False}
    if not text:
        result["warnings"].append("empty_input")
        return result

    structured = _parse_structured_payload(text)
    if structured:
        result["items"] = _dedupe_items(structured)
        result["sources"].append("structured")
        return result

    regex_result = _parse_token_pairs(text)
    if regex_result["items"]:
        result["items"] = _dedupe_items(regex_result["items"])
        result["warnings"].extend(regex_result["warnings"])
        result["ambiguous"] = regex_result["ambiguous"]
        result["sources"].append("deterministic")
        return result

    if llm_fallback:
        llm_items = _normalize_items(llm_fallback(text))
        if llm_items:
            result["items"] = _dedupe_items(llm_items)
            result["used_llm"] = True
            result["sources"].append("llm")
            return result

    result["warnings"].append("no_items_extracted")
    return result


def message_has_structured_cart_payload(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    return bool(_extract_json_like_block(text) or (text.startswith("[") and text.endswith("]")) or (text.startswith("{") and text.endswith("}")))


def message_has_multi_entity_cart_input(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered:
        return False
    return any(joiner in lowered for joiner in [" and ", ",", "&", "+"]) and bool(re.search(r"\d", lowered))


def message_requires_cart_extraction(message: str) -> bool:
    lowered = str(message or "").lower().strip()
    if not lowered:
        return False
    return bool(message_has_structured_cart_payload(lowered) or re.search(r"\d", lowered) or any(token in lowered for token in {"cart", "add", "buy", "order", "want", "need"}))


def _parse_structured_payload(message: str) -> List[Dict[str, Any]]:
    block = _extract_json_like_block(message)
    if not block:
        return []
    parsed = _load_json_like(block)
    if parsed is None:
        return []
    return _normalize_items(_items_from_payload(parsed))


def _extract_json_like_block(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return text
    start_candidates = [idx for idx in [text.find("{"), text.find("[")] if idx >= 0]
    if not start_candidates:
        return ""
    start = min(start_candidates)
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        return ""
    return text[start : end + 1]


def _load_json_like(raw: str) -> Any:
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(raw)
        except Exception:
            continue
    return None


def _items_from_payload(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("items"), list):
        return payload["items"]
    if isinstance(payload.get("arguments"), dict) and isinstance(payload["arguments"].get("items"), list):
        return payload["arguments"]["items"]
    return [payload]


def _parse_token_pairs(message: str) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    warnings: List[str] = []
    ambiguous = False
    for segment in [part.strip() for part in _SEGMENT_SPLIT_RE.split(str(message or "").lower()) if part.strip()]:
        segment_result = _parse_segment(segment)
        items.extend(segment_result["items"])
        warnings.extend(segment_result["warnings"])
        ambiguous = ambiguous or segment_result["ambiguous"]
    normalized = [item for item in _normalize_items(items) if item.get("product_name")]
    return {"items": normalized, "warnings": warnings, "ambiguous": ambiguous}


def _parse_segment(segment: str) -> Dict[str, Any]:
    raw_tokens = _TOKEN_RE.findall(segment)
    tokens = [token for token in raw_tokens if token and token not in _JOINERS]
    items: List[Dict[str, Any]] = []
    warnings: List[str] = []
    ambiguous = False
    i = 0
    pending_quantity: int | None = None

    while i < len(tokens):
        token = tokens[i]
        if token in _LEAD_FILLERS:
            i += 1
            continue
        qty_from_token = _quantity_token(token)
        if qty_from_token is not None:
            pending_quantity = qty_from_token
            i += 1
            name_tokens, new_index = _consume_name_tokens(tokens, i)
            if not name_tokens:
                ambiguous = True
                warnings.append("quantity_without_product")
                continue
            items.append({"product_name": _clean_product_name(" ".join(name_tokens)), "quantity": pending_quantity})
            pending_quantity = None
            i = new_index
            continue

        name_tokens, new_index = _consume_name_tokens(tokens, i)
        if not name_tokens:
            i += 1
            continue
        quantity_after: int | None = None
        if new_index < len(tokens):
            quantity_after = _quantity_token(tokens[new_index])
        product_name = _clean_product_name(" ".join(name_tokens))
        if quantity_after is not None:
            items.append({"product_name": product_name, "quantity": quantity_after})
            i = new_index + 1
            continue
        items.append({"product_name": product_name, "quantity": 1})
        i = new_index

    if not items and pending_quantity is not None:
        ambiguous = True
        warnings.append("dangling_quantity")
    return {"items": items, "warnings": warnings, "ambiguous": ambiguous}


def _consume_name_tokens(tokens: List[str], start: int) -> tuple[List[str], int]:
    consumed: List[str] = []
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token in _LEAD_FILLERS:
            if consumed:
                break
            index += 1
            continue
        if _quantity_token(token) is not None:
            break
        if token in _JOINERS:
            if consumed:
                break
            index += 1
            continue
        consumed.append(token)
        index += 1
        if index < len(tokens) and _quantity_token(tokens[index]) is not None:
            break
    while consumed and consumed[-1] in _TRAIL_FILLERS:
        consumed.pop()
    return consumed, index


def _quantity_token(token: str) -> int | None:
    raw = str(token or "").strip().lower()
    if not raw:
        return None
    if raw.startswith("x") and raw[1:].isdigit():
        return _safe_qty(raw[1:])
    if raw.endswith("x") and raw[:-1].isdigit():
        return _safe_qty(raw[:-1])
    if raw.isdigit():
        return _safe_qty(raw)
    return None


def _clean_product_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(name or "").strip().lower())
    cleaned = re.sub(r"^(?:of|the)\s+", "", cleaned)
    cleaned = re.sub(r"^(?:more)\s+", "", cleaned)
    cleaned = re.sub(r"\b(?:please|pls)$", "", cleaned).strip()
    return cleaned


def _safe_qty(value: Any) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _normalize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("product_name") or item.get("name") or item.get("product") or "").strip()
        if not name:
            continue
        normalized.append({"product_name": _clean_product_name(name), "quantity": _safe_qty(item.get("quantity") or item.get("qty") or 1)})
    return normalized


def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, int] = {}
    for item in items or []:
        name = _clean_product_name(item.get("product_name") or item.get("name") or "")
        if not name:
            continue
        merged[name] = merged.get(name, 0) + _safe_qty(item.get("quantity") or item.get("qty") or 1)
    return [{"product_name": name, "quantity": quantity} for name, quantity in merged.items()]
