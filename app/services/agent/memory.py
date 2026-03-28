from typing import Dict, List

from app.services.state import (
    get_conversation_state,
    get_recent_history,
    append_history,
    update_last_language,
)


def get_state(phone: str, business_id: str) -> Dict:
    return get_conversation_state(phone, business_id)


def get_history(phone: str, business_id: str) -> List[Dict]:
    return get_recent_history(phone, business_id, limit=10)


def save_turn(phone: str, user_msg: str, bot_msg: str, business_id: str):
    append_history(phone, "user", user_msg, business_id)
    append_history(phone, "assistant", bot_msg, business_id)


def update_language(user: Dict, message: str, business_id: str) -> str:
    from app.services.llm import detect_language_style

    style = detect_language_style(message)

    update_last_language(
        user["phone"],
        user["role"],
        style,
        message,
        business_id=business_id,
    )

    return style