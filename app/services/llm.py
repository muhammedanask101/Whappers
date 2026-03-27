import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def call_llm(messages: List[Dict[str, str]], *, json_mode: bool = False, temperature: float = 0.2) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    payload: Dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc

    return data["choices"][0]["message"]["content"].strip()


def detect_language_style(message: str) -> str:
    if re.search(r"[\u0D00-\u0D7F]", message):
        return "malayalam"

    lowered = message.lower()
    manglish_markers = [
        "cheyyu",
        "ayi",
        "undo",
        "venam",
        "nale",
        "innu",
        "entha",
        "aaki",
        "eduthu",
    ]
    if any(token in lowered for token in manglish_markers):
        return "manglish"
    return "english"


def is_confirmation_yes(message: str) -> bool:
    lowered = message.strip().lower()
    return lowered in {
        "yes",
        "y",
        "ok",
        "okay",
        "confirm",
        "confirmed",
        "proceed",
        "sure",
        "അതെ",
        "ശരി",
        "aam",
        "athe",
        "sheri",
    }


def is_confirmation_no(message: str) -> bool:
    lowered = message.strip().lower()
    return lowered in {
        "no",
        "n",
        "cancel",
        "stop",
        "not now",
        "വേണ്ട",
        "ഇല്ല",
        "venda",
        "illa",
    }


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
            "ask_address": "ഡെലിവറി അഡ്രസ് അയയ്ക്കാമോ?",
            "invalid_tool": "ഈ action safe ആയി complete ചെയ്യാൻ കഴിഞ്ഞില്ല. ദയവായി വീണ്ടും പറയൂ.",
            "tool_error": "Request process ചെയ്യുമ്പോൾ ഒരു പ്രശ്നം ഉണ്ടായി.",
            "draft_missing": "തുടരാൻ എനിക്ക് {fields} വേണം.",
            "confirm_create_order": "YES അയച്ചാൽ order place ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും.",
            "confirm_create_offer": "YES അയച്ചാൽ offer create ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും.",
            "confirm_send_broadcast": "YES അയച്ചാൽ broadcast അയക്കും, NO അയച്ചാൽ cancel ചെയ്യും.",
        },
    }
    selected = templates.get(style, templates["english"])
    return selected[key].format(**kwargs)
