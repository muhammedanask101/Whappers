from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List

from app.core.settings import get_settings
from app.services.resilience import get_resilient_caller


def call_openai_chat(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float, tier: str) -> dict[str, Any]:
    settings = get_settings()
    model = settings.openai_models.get("strong" if tier in {"smart", "strong"} else tier, settings.openai_models["balanced"])
    payload: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        settings.openai_api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    def _request() -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
        return {
            "provider": "openai",
            "model": model,
            "content": data["choices"][0]["message"]["content"].strip(),
        }

    return get_resilient_caller("openai-chat", max_retries=2).call(_request)


def call_openai_planner_tool(messages: List[Dict[str, str]], *, tier: str) -> dict[str, Any]:
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

    def _request() -> dict[str, Any]:
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
        arguments = json.loads(tool_calls[0]["function"]["arguments"])
        arguments["provider"] = "openai"
        arguments["model"] = model
        return arguments

    return get_resilient_caller("openai-planner", max_retries=2).call(_request)
