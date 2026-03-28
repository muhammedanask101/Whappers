from typing import Any, Dict, List

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now


def get_conversation_state(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    document = get_collection("conversation_state").find_one({"business_id": business_id, "phone": phone})
    if not document:
        return {"phone": phone, "businessId": business_id, "pendingAction": None, "lastLanguage": "english"}
    return {
        "phone": document["phone"],
        "businessId": document.get("business_id", business_id),
        "role": document.get("role"),
        "pendingAction": document.get("pending_action"),
        "lastLanguage": document.get("last_language") or "english",
        "lastMessage": document.get("last_message"),
    }


def set_pending_action(
    phone: str,
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    role: str,
    action_type: str,
    data: Dict[str, Any],
    last_language: str,
    last_message: str,
) -> None:
    get_collection("conversation_state").update_one(
        {"business_id": business_id, "phone": phone},
        {
            "$set": {
                "business_id": business_id,
                "phone": phone,
                "role": role,
                "pending_action": {"type": action_type, "data": data},
                "last_language": last_language,
                "last_message": last_message,
                "updated_at": utc_now(),
            }
        },
        upsert=True,
    )


def clear_pending_action(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    get_collection("conversation_state").update_one(
        {"business_id": business_id, "phone": phone},
        {"$set": {"pending_action": None, "updated_at": utc_now()}},
        upsert=True,
    )


def update_last_language(phone: str, role: str, language: str, last_message: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    get_collection("conversation_state").update_one(
        {"business_id": business_id, "phone": phone},
        {
            "$set": {
                "business_id": business_id,
                "phone": phone,
                "role": role,
                "last_language": language,
                "last_message": last_message,
                "updated_at": utc_now(),
            }
        },
        upsert=True,
    )


def append_history(phone: str, role: str, content: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    get_collection("chat_history").insert_one(
        {"business_id": business_id, "phone": phone, "role": role, "content": content, "created_at": utc_now()}
    )


def get_recent_history(phone: str, business_id: str = DEFAULT_BUSINESS_ID, limit: int = 10) -> List[Dict[str, str]]:
    cursor = get_collection("chat_history").find({"business_id": business_id, "phone": phone}).sort("created_at", -1).limit(limit)
    items = list(cursor)
    items.reverse()
    return [{"role": item.get("role", "user"), "content": item.get("content", "")} for item in items]
