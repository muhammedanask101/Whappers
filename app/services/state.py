from __future__ import annotations

from typing import Any, Dict, List

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now


DEFAULT_RUNTIME_STATE: Dict[str, Any] = {
    "workflow": {"name": "", "stage": "", "required_slots": [], "filled_slots": {}, "next_question": ""},
    "cart": {"items": [], "address": "", "updated_at": None},
    "goal": {"goal_type": "", "goal_text": "", "status": "idle", "current_plan": [], "completed_steps": [], "progress_score": 0.0, "last_updated_at": None},
    "pending_actions": [],
}


def normalize_runtime_state(document: Dict[str, Any] | None) -> Dict[str, Any]:
    raw_state = dict((document or {}).get("runtime_state") or {})
    state = {
        "workflow": dict(DEFAULT_RUNTIME_STATE["workflow"]),
        "cart": dict(DEFAULT_RUNTIME_STATE["cart"]),
        "goal": dict(DEFAULT_RUNTIME_STATE["goal"]),
        "pending_actions": list(DEFAULT_RUNTIME_STATE["pending_actions"]),
    }
    for key in state:
        if isinstance(raw_state.get(key), type(state[key])):
            if isinstance(state[key], dict):
                state[key].update(raw_state[key])
            else:
                state[key] = list(raw_state[key])
    if not raw_state:
        if document:
            state["workflow"].update(document.get("workflow") or {})
            state["cart"].update(document.get("cart") or {})
            state["goal"].update(document.get("goal_tracking") or {})
    return state


def get_conversation_state(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    document = get_collection("conversation_state").find_one({"business_id": business_id, "phone": phone})
    runtime_state = normalize_runtime_state(document)
    if not document:
        return {
            "phone": phone,
            "businessId": business_id,
            "pendingAction": None,
            "lastLanguage": "english",
            "workflow": runtime_state["workflow"],
            "cart": runtime_state["cart"],
            "intent_history": [],
            "uncertainty": {},
            "last_order_id": "",
            "last_vendor_order_id": "",
            "open_ticket_id": "",
            "goal_tracking": runtime_state["goal"],
            "feedback": {"sentiment": "neutral", "score": 0.0},
            "runtime_state": runtime_state,
        }
    return {
        "phone": document["phone"],
        "businessId": document.get("business_id", business_id),
        "role": document.get("role"),
        "pendingAction": document.get("pending_action"),
        "lastLanguage": document.get("last_language") or "english",
        "lastMessage": document.get("last_message"),
        "workflow": runtime_state["workflow"],
        "cart": runtime_state["cart"],
        "customer_summary": document.get("customer_summary", ""),
        "intent_history": document.get("intent_history", []),
        "uncertainty": document.get("uncertainty", {}),
        "last_order_id": document.get("last_order_id", ""),
        "last_vendor_order_id": document.get("last_vendor_order_id", ""),
        "open_ticket_id": document.get("open_ticket_id", ""),
        "goal_tracking": runtime_state["goal"],
        "feedback": document.get("feedback") or {"sentiment": "neutral", "score": 0.0},
        "last_reflection": document.get("last_reflection") or {},
        "memory_version": document.get("memory_version", 1),
        "runtime_state": runtime_state,
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
    current = get_collection("conversation_state").find_one({"business_id": business_id, "phone": phone}) or {}
    runtime_state = normalize_runtime_state(current)
    runtime_state["pending_actions"] = [{"type": action_type, "data": data}]
    get_collection("conversation_state").update_one(
        {"business_id": business_id, "phone": phone},
        {
            "$set": {
                "business_id": business_id,
                "phone": phone,
                "role": role,
                "pending_action": {"type": action_type, "data": data},
                "runtime_state": runtime_state,
                "last_language": last_language,
                "last_message": last_message,
                "updated_at": utc_now(),
            }
        },
        upsert=True,
    )


def clear_pending_action(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    current = get_collection("conversation_state").find_one({"business_id": business_id, "phone": phone}) or {}
    runtime_state = normalize_runtime_state(current)
    runtime_state["pending_actions"] = []
    get_collection("conversation_state").update_one(
        {"business_id": business_id, "phone": phone},
        {"$set": {"pending_action": None, "runtime_state": runtime_state, "updated_at": utc_now()}},
        upsert=True,
    )


def update_last_language(phone: str, role: str, language: str, last_message: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    get_collection("conversation_state").update_one(
        {"business_id": business_id, "phone": phone},
        {"$set": {"business_id": business_id, "phone": phone, "role": role, "last_language": language, "last_message": last_message, "updated_at": utc_now()}},
        upsert=True,
    )


def append_history(phone: str, role: str, content: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    get_collection("chat_history").insert_one({"business_id": business_id, "phone": phone, "role": role, "content": content, "created_at": utc_now()})


def get_recent_history(phone: str, business_id: str = DEFAULT_BUSINESS_ID, limit: int = 10) -> List[Dict[str, str]]:
    cursor = get_collection("chat_history").find({"business_id": business_id, "phone": phone}).sort("created_at", -1).limit(limit)
    items = list(cursor)
    items.reverse()
    return [{"role": item.get("role", "user"), "content": item.get("content", "")} for item in items]
