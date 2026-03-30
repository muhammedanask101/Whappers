from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from app.core.settings import get_settings
from app.services.resilience import get_resilient_caller


def _gemini_endpoint(model: str) -> str:
    settings = get_settings()
    api_key = urllib.parse.quote(settings.gemini_api_key, safe="")
    return f"{settings.gemini_api_url}/{model}:generateContent?key={api_key}"


def _gemini_payload(messages: List[Dict[str, str]], *, temperature: float) -> dict[str, Any]:
    joined = "\n".join(f"{row.get('role', 'user')}: {row.get('content', '')}" for row in messages)
    return {
        "contents": [{"role": "user", "parts": [{"text": joined}]}],
        "generationConfig": {"temperature": temperature},
    }


def call_gemini_chat(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> dict[str, Any]:
    settings = get_settings()
    model = settings.gemini_models.get("strong" if tier in {"smart", "strong"} else tier, settings.gemini_models["balanced"])
    payload = _gemini_payload(messages, temperature=temperature)
    request = urllib.request.Request(
        _gemini_endpoint(model),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _request() -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Gemini request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        content = "\n".join(str(part.get("text") or "").strip() for part in parts if part.get("text")).strip()
        if json_mode and content and not content.lstrip().startswith("{"):
            content = json.dumps({"message": content})
        return {"provider": "gemini", "model": model, "content": content}

    return get_resilient_caller("gemini-chat", max_retries=1).call(_request)
