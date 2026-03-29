from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable, Dict, List


LLMExtractor = Callable[[str], List[Dict[str, Any]]]


_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:,| and | & |\+ )\s*", re.IGNORECASE)
_NAME_QTY_RE = re.compile(r"^(?P<name>[a-z][a-z0-9\s\-]{1,60}?)\s*(?:x|\*)\s*(?P<qty>\d+)$", re.IGNORECASE)
_QTY_NAME_RE = re.compile(r"^(?P<qty>\d+)\s+(?P<name>[a-z][a-z0-9\s\-]{1,60})$", re.IGNORECASE)
_WANT_QTY_NAME_RE = re.compile(
    r"^(?:i\s+want|want|need|add|buy|order|please\s+add|please)\s+(?P<qty>\d+)\s+(?P<name>[a-z][a-z0-9\s\-]{1,60})$",
    re.IGNORECASE,
)
_TRAILING_FILLERS_RE = re.compile(r"\b(?:please|pls|for me|to cart|in cart|into cart|cart)$", re.IGNORECASE)


def extract_cart_candidates(message: str, *, llm_fallback: LLMExtractor | None = None) -> Dict[str, Any]:
    text = str(message or "").strip()
    result: Dict[str, Any] = {"items": [], "warnings": [], "used_llm": False, "sources": []}
    if not text:
        result["warnings"].append("empty_input")
        return result

    structured = _parse_structured_payload(text)
    if structured:
        result["items"].extend(structured)
        result["sources"].append("structured")

    regex_items = [] if structured else _parse_regex_items(text)
    if regex_items:
        result["items"].extend(regex_items)
        result["sources"].append("regex")

    deduped = _dedupe_items(result["items"])
    if deduped:
        result["items"] = deduped
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
    return any(token in lowered for token in [" and ", ",", "&"]) and bool(re.search(r"\d", lowered))


def message_requires_cart_extraction(message: str) -> bool:
    lowered = str(message or "").lower().strip()
    if not lowered:
        return False
    return (
        message_has_structured_cart_payload(lowered)
        or bool(_QTY_NAME_RE.search(lowered))
        or bool(_NAME_QTY_RE.search(lowered))
        or bool(_WANT_QTY_NAME_RE.search(lowered))
        or message_has_multi_entity_cart_input(lowered)
    )


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
    end_curly = text.rfind("}")
    end_square = text.rfind("]")
    end = max(end_curly, end_square)
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


def _parse_regex_items(message: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for segment in _candidate_segments(message):
        cleaned = _normalize_segment(segment)
        if not cleaned:
            continue
        match = _NAME_QTY_RE.match(cleaned) or _QTY_NAME_RE.match(cleaned) or _WANT_QTY_NAME_RE.match(cleaned)
        if not match:
            fallback_name = _fallback_segment_name(cleaned)
            if fallback_name:
                items.append({"product_name": fallback_name, "quantity": 1})
            continue
        name = _clean_product_name(match.group("name"))
        qty = _safe_qty(match.group("qty"))
        if name and qty:
            items.append({"product_name": name, "quantity": qty})
    return items


def _candidate_segments(message: str) -> List[str]:
    text = str(message or "").replace("\n", " ").strip()
    if not text:
        return []
    segments = [text]
    if _SEGMENT_SPLIT_RE.search(text):
        segments = [segment.strip() for segment in _SEGMENT_SPLIT_RE.split(text) if segment.strip()]
    return segments


def _normalize_segment(segment: str) -> str:
    lowered = re.sub(r"\s+", " ", str(segment or "").strip().lower())
    lowered = lowered.replace(" x ", " x ")
    lowered = re.sub(r"^(?:cart|add to cart)\s+", "", lowered)
    lowered = _TRAILING_FILLERS_RE.sub("", lowered).strip()
    return lowered


def _clean_product_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(name or "").strip().lower())
    cleaned = re.sub(r"^(?:of|the)\s+", "", cleaned)
    cleaned = re.sub(r"\b(?:please|pls)$", "", cleaned).strip()
    return cleaned


def _fallback_segment_name(segment: str) -> str:
    cleaned = _clean_product_name(segment)
    if not cleaned:
        return ""
    filler = {"i", "want", "need", "add", "buy", "order", "please", "cart", "show", "products"}
    tokens = cleaned.split()
    if not tokens or all(token in filler for token in tokens):
        return ""
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
        name = str(
            item.get("product_name")
            or item.get("name")
            or item.get("product")
            or ""
        ).strip()
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
