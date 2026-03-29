from __future__ import annotations

import json
import os
import re
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


def call_llm(
    messages: List[Dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.2,
    tier: str = "balanced",
) -> str:
    provider = _select_provider(tier)
    content = _dispatch(provider, messages, json_mode=json_mode, temperature=temperature, tier=tier)
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
            "message": content.strip() or "Please tell me a bit more.",
            "confidence": 0.25,
        },
        ensure_ascii=False,
    )


def call_agent_llm(messages: List[Dict[str, str]], *, tier: str = "balanced") -> Dict[str, Any]:
    raw = call_llm(messages, json_mode=True, temperature=0.15 if tier == "cheap" else 0.2, tier=tier)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"thought": "parse_fallback", "action": "respond", "arguments": {}, "message": raw, "confidence": 0.25}


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
        if provider == "openai" and has_openai:
            return "openai"
        if provider == "gemini" and has_gemini:
            return "gemini"
    if has_openai:
        return "openai"
    if has_gemini:
        return "gemini"
    raise RuntimeError("No LLM provider is configured. Set OPENAI_API_KEY or GEMINI_API_KEY.")


def _dispatch(provider: str, messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> str:
    errors: List[str] = []
    providers = [provider]
    if provider == "openai" and os.getenv("GEMINI_API_KEY", "").strip():
        providers.append("gemini")
    if provider == "gemini" and os.getenv("OPENAI_API_KEY", "").strip():
        providers.append("openai")

    for candidate in providers:
        try:
            if candidate == "openai":
                return _call_openai(messages, json_mode=json_mode, temperature=temperature, tier=tier)
            return _call_gemini(messages, json_mode=json_mode, temperature=temperature, tier=tier)
        except Exception as exc:
            errors.append(str(exc))
            continue
    raise RuntimeError("; ".join(errors))


def _call_openai(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    payload: Dict[str, Any] = {
        "model": OPENAI_MODELS.get(tier, OPENAI_MODELS["balanced"]),
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
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
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
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc
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
        "message": str(payload.get("message") or "").strip(),
        "confidence": max(0.0, min(confidence_value, 1.0)),
    }


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
