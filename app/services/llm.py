from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

OPENAI_MODELS = {
    "cheap": os.getenv("OPENAI_CHEAP_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
    "balanced": os.getenv("OPENAI_BALANCED_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
    "smart": os.getenv("OPENAI_SMART_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
}
GEMINI_MODELS = {
    "cheap": os.getenv("GEMINI_CHEAP_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
    "balanced": os.getenv("GEMINI_BALANCED_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
    "smart": os.getenv("GEMINI_SMART_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
}

PROVIDER_BACKOFF_UNTIL: Dict[str, float] = {"openai": 0.0, "gemini": 0.0}


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
    provider = _select_provider(tier)
    _log_llm(
        "call_llm_start",
        provider=provider,
        tier=tier,
        json_mode=json_mode,
        temperature=temperature,
        message_count=len(messages),
        preview=(messages[-1].get("content", "")[:160] if messages else ""),
    )
    content = _dispatch(provider, messages, json_mode=json_mode, temperature=temperature, tier=tier)
    _log_llm("call_llm_success", provider=provider, tier=tier, response_preview=content[:240])
    if not json_mode:
        return content
    normalized = normalize_llm_json(content)
    if normalized:
        return json.dumps(normalized, ensure_ascii=False)
    return json.dumps(
        {
            "thought": "fallback",
            "action": "respond",
            "arguments": {},
            "message": safe_user_facing_text(content) or "Please tell me a bit more.",
            "confidence": 0.25,
        },
        ensure_ascii=False,
    )


def call_agent_llm(messages: List[Dict[str, str]], *, tier: str = "balanced") -> Dict[str, Any]:
    _log_llm("call_agent_llm_start", tier=tier, message_count=len(messages))
    raw = call_llm(messages, json_mode=True, temperature=0.15 if tier == "cheap" else 0.2, tier=tier)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "message" in parsed:
            parsed["message"] = safe_user_facing_text(parsed.get("message", ""))
        _log_llm("call_agent_llm_success", tier=tier, action=parsed.get("action"), confidence=parsed.get("confidence"))
        return parsed
    except json.JSONDecodeError:
        _log_llm("call_agent_llm_parse_fallback", tier=tier, raw_preview=raw[:240])
        return {"thought": "parse_fallback", "action": "respond", "arguments": {}, "message": safe_user_facing_text(raw), "confidence": 0.25}


def _select_provider(tier: str = "balanced") -> str:
    configured = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    has_openai = bool(os.getenv("OPENAI_API_KEY", "").strip())
    has_gemini = bool(os.getenv("GEMINI_API_KEY", "").strip())

    if configured == "openai":
        if not has_openai:
            raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is missing.")
        return "openai"
    if configured == "gemini":
        if not has_gemini:
            raise RuntimeError("LLM_PROVIDER=gemini but GEMINI_API_KEY is missing.")
        return "gemini"
    preferred = os.getenv("LLM_PROVIDER_PRIORITY", "openai,gemini").split(",")
    preferred = [item.strip().lower() for item in preferred if item.strip()]
    for provider in preferred:
        if _provider_in_backoff(provider):
            _log_llm("provider_backoff_skip", provider=provider, tier=tier, wait_seconds=round(PROVIDER_BACKOFF_UNTIL.get(provider, 0.0) - time.time(), 2))
            continue
        if provider == "openai" and has_openai:
            _log_llm("provider_selected", provider="openai", tier=tier, mode="preferred")
            return "openai"
        if provider == "gemini" and has_gemini:
            _log_llm("provider_selected", provider="gemini", tier=tier, mode="preferred")
            return "gemini"
    if has_openai and not _provider_in_backoff("openai"):
        _log_llm("provider_selected", provider="openai", tier=tier, mode="fallback")
        return "openai"
    if has_gemini and not _provider_in_backoff("gemini"):
        _log_llm("provider_selected", provider="gemini", tier=tier, mode="fallback")
        return "gemini"
    if has_openai or has_gemini:
        raise RuntimeError("Configured LLM providers are temporarily in backoff after recent failures.")
    raise RuntimeError("No LLM provider is configured. Set OPENAI_API_KEY or GEMINI_API_KEY.")


def _dispatch(provider: str, messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> str:
    errors: List[str] = []
    providers = [provider]
    if provider == "openai" and os.getenv("GEMINI_API_KEY", "").strip():
        providers.append("gemini")
    if provider == "gemini" and os.getenv("OPENAI_API_KEY", "").strip():
        providers.append("openai")

    for candidate in providers:
        if _provider_in_backoff(candidate):
            _log_llm("provider_backoff_skip", provider=candidate, tier=tier, wait_seconds=round(PROVIDER_BACKOFF_UNTIL.get(candidate, 0.0) - time.time(), 2))
            continue
        try:
            _log_llm("provider_attempt", provider=candidate, tier=tier, json_mode=json_mode)
            if candidate == "openai":
                return _call_openai(messages, json_mode=json_mode, temperature=temperature, tier=tier)
            return _call_gemini(messages, json_mode=json_mode, temperature=temperature, tier=tier)
        except Exception as exc:
            _log_llm("provider_attempt_failed", provider=candidate, tier=tier, error=str(exc))
            errors.append(str(exc))
            continue
    raise RuntimeError("; ".join(errors))


def _call_openai(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = OPENAI_MODELS.get(tier, OPENAI_MODELS["balanced"])
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        _log_llm("external_api_request", provider="openai", model=model, tier=tier, json_mode=json_mode, prompt_chars=sum(len(message.get("content", "")) for message in messages))
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        _mark_provider_backoff("openai", detail)
        _log_llm("external_api_error", provider="openai", model=model, tier=tier, error=f"{exc.code} {detail[:240]}")
        raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        _log_llm("external_api_error", provider="openai", model=model, tier=tier, error=str(exc.reason))
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
    _log_llm("external_api_response", provider="openai", model=model, tier=tier, choice_count=len(data.get("choices", [])))
    return data["choices"][0]["message"]["content"].strip()


def _call_gemini(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = GEMINI_MODELS.get(tier, GEMINI_MODELS["balanced"])
    prompt = _messages_to_text(messages, json_mode=json_mode)
    payload: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    request = urllib.request.Request(
        GEMINI_API_URL.format(model=model, api_key=api_key),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        _log_llm("external_api_request", provider="gemini", model=model, tier=tier, json_mode=json_mode, prompt_chars=len(prompt))
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        _mark_provider_backoff("gemini", detail)
        _log_llm("external_api_error", provider="gemini", model=model, tier=tier, error=f"{exc.code} {detail[:240]}")
        raise RuntimeError(f"Gemini request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        _log_llm("external_api_error", provider="gemini", model=model, tier=tier, error=str(exc.reason))
        raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc
    _log_llm("external_api_response", provider="gemini", model=model, tier=tier, candidate_count=len(data.get("candidates", [])))
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(str(part.get("text", "")) for part in parts).strip()


def _messages_to_text(messages: List[Dict[str, str]], *, json_mode: bool) -> str:
    rendered = [f"{message.get('role', 'user').upper()}: {message.get('content', '')}" for message in messages]
    prompt = "\n\n".join(rendered)
    if json_mode:
        prompt += (
            "\n\nReturn a single JSON object with keys thought, action, arguments, message, confidence. "
            "Do not include markdown fences."
        )
    return prompt


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
    candidate = text.strip()
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
    action = payload.get("action")
    if action is not None:
        action = str(action).strip() or "respond"
    confidence = payload.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.4
    return {
        "thought": str(payload.get("thought") or "").strip(),
        "action": action or "respond",
        "arguments": arguments,
        "message": safe_user_facing_text(payload.get("message") or ""),
        "confidence": max(0.0, min(confidence_value, 1.0)),
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
    compact = text.replace("\n", " ").strip()
    return compact[:1500]


def _provider_in_backoff(provider: str) -> bool:
    return PROVIDER_BACKOFF_UNTIL.get(provider, 0.0) > time.time()


def _mark_provider_backoff(provider: str, detail: str) -> None:
    lowered = str(detail or "").lower()
    if "429" not in lowered and "quota" not in lowered and "rate limit" not in lowered and "resource_exhausted" not in lowered:
        return
    retry_seconds = _extract_retry_seconds(detail)
    PROVIDER_BACKOFF_UNTIL[provider] = max(PROVIDER_BACKOFF_UNTIL.get(provider, 0.0), time.time() + retry_seconds)
    _log_llm("provider_backoff_set", provider=provider, wait_seconds=retry_seconds)


def _extract_retry_seconds(detail: str) -> float:
    text = str(detail or "")
    for pattern in (r'"retryDelay"\s*:\s*"(\d+)s"', r"retry in\s+(\d+(?:\.\d+)?)s"):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return max(5.0, min(float(match.group(1)), 180.0))
            except ValueError:
                break
    return 45.0


def detect_language_style(message: str) -> str:
    if re.search(r"[\u0D00-\u0D7F]", message):
        return "malayalam"
    lowered = message.lower()
    manglish_markers = [
        "cheyyu", "cheythu", "ayi", "undo", "venam", "vende", "nale", "innu", "entha",
        "enthaanu", "aaki", "eduthu", "tharuo", "kodukku", "evide", "poyi", "ishtam",
        "venel", "alle", "aayal", "ningalude", "sadhanam", "sheri",
    ]
    if any(token in lowered for token in manglish_markers):
        return "manglish"
    return "english"


def is_confirmation_yes(message: str) -> bool:
    lowered = message.strip().lower()
    return lowered in {"yes", "y", "ok", "okay", "confirm", "confirmed", "proceed", "sure", "aam", "athe", "sheri"}


def is_confirmation_no(message: str) -> bool:
    lowered = message.strip().lower()
    return lowered in {"no", "n", "cancel", "stop", "not now", "venda", "illa"}
