from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


def extract_first_json_object(text: str) -> Optional[str]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def safe_parse_json(text: str) -> Optional[Dict[str, Any]]:
    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    if not candidate.startswith("{") or not candidate.endswith("}"):
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    confidence = payload.get("confidence")
    uncertainty = payload.get("uncertainty")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.4
    try:
        uncertainty_value = float(uncertainty)
    except (TypeError, ValueError):
        uncertainty_value = max(0.0, 1.0 - confidence_value)
    return {
        "thought": str(payload.get("thought") or "").strip(),
        "action": str(payload.get("action") or "respond").strip() or "respond",
        "arguments": arguments,
        "message": safe_user_facing_text(payload.get("message") or ""),
        "confidence": max(0.0, min(confidence_value, 1.0)),
        "uncertainty": max(0.0, min(uncertainty_value, 1.0)),
        "plan_steps": list(payload.get("plan_steps") or []),
        "goal": dict(payload.get("goal") or {}),
        "provider": str(payload.get("provider") or "").strip(),
        "model": str(payload.get("model") or "").strip(),
    }


def normalize_llm_json(text: str) -> Optional[Dict[str, Any]]:
    parsed = safe_parse_json(text)
    if parsed:
        return _normalize_payload(parsed)
    extracted = extract_first_json_object(text)
    if extracted:
        parsed = safe_parse_json(extracted)
        if parsed:
            return _normalize_payload(parsed)
    return None


def safe_user_facing_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = safe_parse_json(text) or safe_parse_json(extract_first_json_object(text) or "")
    if isinstance(parsed, dict):
        nested = str(parsed.get("message") or "").strip()
        if nested and nested != text:
            return safe_user_facing_text(nested)
    if text.startswith("{") and text.endswith("}"):
        return "Please tell me a bit more so I can help properly."
    if any(marker in text for marker in ['"thought"', '"action"', '"arguments"', '"confidence"']):
        return "Please tell me a bit more so I can help properly."
    return text.replace("\n", " ").strip()[:1500]


def detect_language_style(message: str) -> str:
    if re.search(r"[\u0D00-\u0D7F]", message):
        return "malayalam"
    lowered = message.lower()
    manglish_markers = ["cheyyu", "cheythu", "ayi", "undo", "venam", "vende", "nale", "innu", "entha", "ningalude", "sheri"]
    if any(token in lowered for token in manglish_markers):
        return "manglish"
    return "english"


def is_confirmation_yes(message: str) -> bool:
    return message.strip().lower() in {"yes", "y", "ok", "okay", "confirm", "confirmed", "proceed", "sure", "aam", "athe", "sheri"}


def is_confirmation_no(message: str) -> bool:
    return message.strip().lower() in {"no", "n", "cancel", "stop", "not now", "venda", "illa"}
