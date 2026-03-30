from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from app.core.settings import get_settings
from app.services.resilience import ResilientClientError, get_resilient_caller


def _log_llm(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[llm] {event} {normalized}".rstrip())


def call_llm(
    messages: List[Dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    tier: str = "balanced",
) -> str:
    provider = _select_provider()
    _log_llm("call_llm_start", provider=provider, tier=tier, json_mode=json_mode, temperature=temperature, message_count=len(messages))
    if provider == "openai":
        return _call_openai(messages, json_mode=json_mode, temperature=temperature, tier=tier)
    return _call_gemini(messages, json_mode=json_mode, temperature=temperature, tier=tier)


def call_agent_llm(messages: List[Dict[str, str]], *, tier: str = "balanced") -> Dict[str, Any]:
    settings = get_settings()
    if settings.openai_api_key:
        try:
            return _call_openai_planner_tool(messages, tier=tier)
        except Exception as exc:
            _log_llm("tool_call_failed", tier=tier, error=str(exc))
    raw = call_llm(messages, json_mode=True, temperature=0.15 if tier == "cheap" else 0.2, tier=tier)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"thought": "parse_fallback", "action": "respond", "arguments": {}, "message": safe_user_facing_text(raw), "confidence": 0.25}
    payload["message"] = safe_user_facing_text(payload.get("message", ""))
    return _normalize_payload(payload)


def _select_provider() -> str:
    settings = get_settings()
    if settings.openai_api_key:
        return "openai"
    if settings.environment != "test":
        raise RuntimeError("OPENAI_API_KEY is missing.")
    return "openai"


def _call_openai(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> str:
    settings = get_settings()
    model = settings.openai_models.get("strong" if tier == "smart" else tier, settings.openai_models["balanced"])
    payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        settings.openai_api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    def _request() -> str:
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
        return data["choices"][0]["message"]["content"].strip()

    return get_resilient_caller("openai-chat", max_retries=2).call(_request)


def _call_openai_planner_tool(messages: List[Dict[str, str]], *, tier: str) -> Dict[str, Any]:
    settings = get_settings()
    model = settings.openai_models.get("strong" if tier in {"smart", "strong"} else tier, settings.openai_models["balanced"])
    tool_schema = {
        "type": "function",
        "function": {
            "name": "plan_agent_step",
            "description": "Choose the next deterministic tool call or direct response for the commerce agent.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "thought": {"type": "string"},
                    "action": {"type": "string"},
                    "message": {"type": "string"},
                    "confidence": {"type": "number"},
                    "uncertainty": {"type": "number"},
                    "arguments": {"type": "object"},
                    "plan_steps": {"type": "array", "items": {"type": "object"}},
                    "goal": {"type": "object"},
                },
                "required": ["action", "arguments", "message", "confidence", "uncertainty"],
            },
        },
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "tools": [tool_schema],
        "tool_choice": {"type": "function", "function": {"name": "plan_agent_step"}},
    }
    request = urllib.request.Request(
        settings.openai_api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    def _request() -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OpenAI planner tool failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI planner tool failed: {exc.reason}") from exc
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            raise RuntimeError("Planner returned no tool call.")
        arguments = tool_calls[0]["function"]["arguments"]
        return _normalize_payload(json.loads(arguments))

    return get_resilient_caller("openai-planner", max_retries=2).call(_request)


def _call_gemini(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> str:
    raise RuntimeError("Gemini is no longer used in this runtime.")


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
    }


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
