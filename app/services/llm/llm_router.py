from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.settings import get_settings
from app.services.llm.gemini_client import call_gemini_chat
from app.services.llm.openai_client import call_openai_chat, call_openai_planner_tool
from app.services.llm.utils import normalize_llm_json, safe_user_facing_text


_LAST_ROUTE_STATUS: dict[str, Any] = {"provider": "", "model": "", "fallback_used": False}


def _log_llm(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[llm] {event} {normalized}".rstrip())


def get_llm_router_status() -> dict[str, Any]:
    return dict(_LAST_ROUTE_STATUS)


def _available_providers() -> list[str]:
    settings = get_settings()
    providers: list[str] = []
    for provider in settings.llm_provider_priority or ["openai", "gemini"]:
        if provider == "openai" and settings.openai_api_key:
            providers.append(provider)
        elif provider == "gemini" and settings.gemini_api_key:
            providers.append(provider)
    return providers


def _route_chat(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> dict[str, Any]:
    settings = get_settings()
    providers = _available_providers()
    if not providers:
        if settings.environment != "test":
            raise RuntimeError("No LLM provider configured.")
        return {"provider": "deterministic", "model": "none", "content": ""}

    errors: list[str] = []
    for index, provider in enumerate(providers):
        try:
            if provider == "openai":
                result = call_openai_chat(messages, json_mode=json_mode, temperature=temperature, tier=tier)
            else:
                result = call_gemini_chat(messages, json_mode=json_mode, temperature=temperature, tier=tier)
            _LAST_ROUTE_STATUS.update({"provider": result["provider"], "model": result["model"], "fallback_used": index > 0})
            return result
        except Exception as exc:
            errors.append(f"{provider}:{exc}")
            _log_llm("provider_failed", provider=provider, tier=tier, error=str(exc))
            continue
    raise RuntimeError(" | ".join(errors) or "No LLM provider available.")


def call_llm(
    messages: List[Dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    tier: str = "balanced",
) -> str:
    try:
        result = _route_chat(messages, json_mode=json_mode, temperature=temperature, tier=tier)
    except Exception as exc:
        _log_llm("raw_router_failed", tier=tier, error=str(exc))
        fallback = _deterministic_raw_fallback(json_mode=json_mode)
        return fallback
    return result.get("content", "")


def call_agent_llm(messages: List[Dict[str, str]], *, tier: str = "balanced", second_pass: bool = False) -> Dict[str, Any]:
    settings = get_settings()
    if settings.openai_api_key:
        try:
            payload = call_openai_planner_tool(messages, tier="strong" if second_pass and tier != "cheap" else tier)
            _LAST_ROUTE_STATUS.update({"provider": payload.get("provider", "openai"), "model": payload.get("model", ""), "fallback_used": False})
            return _normalize_payload(payload)
        except Exception as exc:
            _log_llm("planner_tool_failed", tier=tier, second_pass=second_pass, error=str(exc))

    try:
        raw = _route_chat(messages, json_mode=True, temperature=0.15 if tier == "cheap" else 0.2, tier="strong" if second_pass and tier != "cheap" else tier)
        payload = normalize_llm_json(raw.get("content", "")) or {
            "thought": "parse_fallback",
            "action": "respond",
            "arguments": {},
            "message": safe_user_facing_text(raw.get("content", "")),
            "confidence": 0.25,
            "uncertainty": 0.75,
            "provider": raw.get("provider", ""),
            "model": raw.get("model", ""),
        }
        payload["provider"] = raw.get("provider", payload.get("provider", ""))
        payload["model"] = raw.get("model", payload.get("model", ""))
        return _normalize_payload(payload)
    except Exception as exc:
        _log_llm("router_failed", tier=tier, second_pass=second_pass, error=str(exc))
        return _deterministic_planner_fallback()


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        confidence = float(payload.get("confidence") or 0.4)
    except (TypeError, ValueError):
        confidence = 0.4
    try:
        uncertainty = float(payload.get("uncertainty") or max(0.0, 1.0 - confidence))
    except (TypeError, ValueError):
        uncertainty = max(0.0, 1.0 - confidence)
    return {
        "thought": str(payload.get("thought") or "").strip(),
        "action": str(payload.get("action") or "respond").strip() or "respond",
        "arguments": arguments,
        "message": safe_user_facing_text(payload.get("message") or ""),
        "confidence": max(0.0, min(confidence, 1.0)),
        "uncertainty": max(0.0, min(uncertainty, 1.0)),
        "plan_steps": list(payload.get("plan_steps") or []),
        "goal": dict(payload.get("goal") or {}),
        "provider": str(payload.get("provider") or ""),
        "model": str(payload.get("model") or ""),
    }


def _deterministic_planner_fallback() -> Dict[str, Any]:
    _LAST_ROUTE_STATUS.update({"provider": "deterministic", "model": "fallback", "fallback_used": True})
    return {
        "thought": "deterministic_fallback_after_provider_failure",
        "action": "respond",
        "arguments": {},
        "message": "Please tell me a bit more so I can help properly.",
        "confidence": 0.2,
        "uncertainty": 0.8,
        "plan_steps": [],
        "goal": {},
        "provider": "deterministic",
        "model": "fallback",
    }


def _deterministic_raw_fallback(*, json_mode: bool) -> str:
    _LAST_ROUTE_STATUS.update({"provider": "deterministic", "model": "fallback", "fallback_used": True})
    if json_mode:
        return json.dumps({"message": "Please tell me a bit more so I can help properly."})
    return "Please tell me a bit more so I can help properly."
