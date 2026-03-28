import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def call_llm(messages: List[Dict[str, str]], *, json_mode: bool = False, temperature: float = 0.2) -> str:
    provider = _select_provider()
    if provider == "openai":
        print("LLM provider: openai")
        content = _call_openai(messages, json_mode=json_mode, temperature=temperature)
    elif provider == "gemini":
        print("LLM provider: gemini")
        content = _call_gemini(messages, json_mode=json_mode, temperature=temperature)
    else:
        raise RuntimeError("No LLM provider is configured.")

    if not json_mode:
        return content

    normalized = normalize_llm_json(content)
    if normalized:
        return json.dumps(normalized, ensure_ascii=False)
    return json.dumps({"thought": "fallback", "action": None, "arguments": {}, "message": content.strip() or "I can help with that."}, ensure_ascii=False)


def _select_provider() -> str:
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
    if has_openai:
        return "openai"
    if has_gemini:
        return "gemini"
    raise RuntimeError("No LLM provider is configured. Set OPENAI_API_KEY or GEMINI_API_KEY.")


def _call_openai(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    payload: Dict[str, Any] = {"model": DEFAULT_OPENAI_MODEL, "messages": messages, "temperature": temperature}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
    return data["choices"][0]["message"]["content"].strip()


def _call_gemini(messages: List[Dict[str, str]], *, json_mode: bool, temperature: float) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
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
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini request failed: {exc.reason}") from exc

    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "\n".join(str(part.get("text", "")) for part in parts).strip()
    return text


def _messages_to_text(messages: List[Dict[str, str]], *, json_mode: bool) -> str:
    rendered = []
    for message in messages:
        rendered.append(f"{message.get('role', 'user').upper()}: {message.get('content', '')}")
    prompt = "\n\n".join(rendered)
    if json_mode:
        prompt += (
            "\n\nReturn a single JSON object with keys thought, action, arguments, and message. "
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
        action = str(action).strip() or None
    return {
        "thought": str(payload.get("thought") or "").strip(),
        "action": action,
        "arguments": arguments,
        "message": str(payload.get("message") or "").strip(),
    }


def detect_language_style(message: str) -> str:
    if re.search(r"[\u0D00-\u0D7F]", message):
        return "malayalam"
    lowered = message.lower()
    manglish_markers = ["cheyyu", "ayi", "undo", "venam", "nale", "innu", "entha", "aaki", "eduthu"]
    if any(token in lowered for token in manglish_markers):
        return "manglish"
    return "english"


def is_confirmation_yes(message: str) -> bool:
    lowered = message.strip().lower()
    return lowered in {"yes", "y", "ok", "okay", "confirm", "confirmed", "proceed", "sure", "aam", "athe", "sheri"}


def is_confirmation_no(message: str) -> bool:
    lowered = message.strip().lower()
    return lowered in {"no", "n", "cancel", "stop", "not now", "venda", "illa"}


def localize_message(key: str, style: str, **kwargs: str) -> str:
    templates = {
        "english": {
            "action_cancelled": "Cancelled. No changes were made.",
            "ask_address": "Please share your delivery address.",
            "invalid_tool": "I couldn't safely complete that action. Please rephrase it.",
            "tool_error": "Something went wrong while processing that request.",
            "draft_missing": "I still need {fields} to continue.",
            "confirm_create_order": "Reply YES to place this order or NO to cancel.",
            "confirm_create_offer": "Reply YES to create this offer or NO to cancel.",
            "confirm_send_broadcast": "Reply YES to send this broadcast or NO to cancel.",
        },
        "manglish": {
            "action_cancelled": "Cancel cheythu. Oru maattavum cheythilla.",
            "ask_address": "Delivery address ayachu tharamo?",
            "invalid_tool": "I action safe ayi complete cheyyan pattiyilla. Onnu vere reethiyil parayu.",
            "tool_error": "Request process cheyyumbo oru prashnam undayi.",
            "draft_missing": "Continue cheyyan enikku {fields} venam.",
            "confirm_create_order": "YES ayachal order place cheyyam, NO ayachal cancel cheyyam.",
            "confirm_create_offer": "YES ayachal offer create cheyyam, NO ayachal cancel cheyyam.",
            "confirm_send_broadcast": "YES ayachal broadcast ayakkam, NO ayachal cancel cheyyam.",
        },
        "malayalam": {
            "action_cancelled": "Cancel ചെയ്തു. മാറ്റങ്ങളൊന്നും വരുത്തിയിട്ടില്ല.",
            "ask_address": "ഡെലിവറി അഡ്രസ് അയക്കാമോ?",
            "invalid_tool": "ഈ action safe ആയി complete ചെയ്യാൻ കഴിഞ്ഞില്ല. ദയവായി വീണ്ടും പറയൂ.",
            "tool_error": "Request process ചെയ്യുമ്പോൾ ഒരു പ്രശ്നം ഉണ്ടായി.",
            "draft_missing": "തുടരാൻ എനിക്ക് {fields} വേണം.",
            "confirm_create_order": "YES അയച്ചാൽ order place ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും.",
            "confirm_create_offer": "YES അയച്ചാൽ offer create ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും.",
            "confirm_send_broadcast": "YES അയച്ചാൽ broadcast അയക്കും, NO അയച്ചാൽ cancel ചെയ്യും.",
        },
    }
    return templates.get(style, templates["english"])[key].format(**kwargs)
