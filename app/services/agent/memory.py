from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now
from app.services.agent.learning import get_learning_profile, recent_events, tokenize
from app.services.agent.prompts import summarize_memory_payload
from app.services.agent.semantic_memory import remember, retrieve, summarize_for_prompt
from app.services.intelligence.customer_profile import get_customer_profile
from app.services.state import append_history, get_conversation_state, get_recent_history, update_last_language
from app.services.vendor import get_business_profile


DEFAULT_STATE = {
    "phone": "",
    "businessId": DEFAULT_BUSINESS_ID,
    "role": "customer",
    "pendingAction": None,
    "lastLanguage": "english",
    "lastMessage": "",
    "workflow": {
        "name": "",
        "stage": "",
        "required_slots": [],
        "filled_slots": {},
        "next_question": "",
    },
    "cart": {"items": [], "address": "", "updated_at": None},
    "customer_summary": "",
    "intent_history": [],
    "uncertainty": {"score": 0.0, "reason": ""},
    "last_order_id": "",
    "last_vendor_order_id": "",
    "open_ticket_id": "",
    "goal_tracking": {
        "goal_type": "",
        "goal_text": "",
        "status": "idle",
        "current_plan": [],
        "completed_steps": [],
        "progress_score": 0.0,
        "last_updated_at": None,
    },
    "feedback": {"sentiment": "neutral", "score": 0.0},
    "last_reflection": {},
    "last_tool_gap": {},
    "memory_version": 3,
}


def get_state(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    raw = get_conversation_state(phone, business_id)
    state = dict(DEFAULT_STATE)
    state.update(raw)
    state["phone"] = phone
    state["businessId"] = business_id
    if not isinstance(state.get("workflow"), dict):
        state["workflow"] = dict(DEFAULT_STATE["workflow"])
    if not isinstance(state.get("cart"), dict):
        state["cart"] = dict(DEFAULT_STATE["cart"])
    if not isinstance(state.get("intent_history"), list):
        state["intent_history"] = []
    if not isinstance(state.get("goal_tracking"), dict):
        state["goal_tracking"] = dict(DEFAULT_STATE["goal_tracking"])
    return state


def get_history(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> List[Dict[str, str]]:
    return get_recent_history(phone, business_id, limit=14)


def get_summary(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    document = get_collection("conversation_summaries").find_one(
        {"business_id": business_id, "phone": phone}
    )
    if document:
        return document
    return {
        "business_id": business_id,
        "phone": phone,
        "summary": "",
        "top_intents": [],
        "salient_entities": {},
        "preferences": [],
        "last_updated_at": None,
    }


def build_memory_bundle(phone: str, business_id: str, state: Dict[str, Any], style: str) -> Dict[str, Any]:
    history = get_history(phone, business_id)
    summary = get_summary(phone, business_id)
    profile = get_customer_profile(phone, business_id)
    learning = get_learning_profile(phone, business_id)
    events = recent_events(business_id, phone=phone, limit=8)
    business = get_business_profile(business_id)
    semantic_rows = retrieve(phone, business_id, query=_semantic_query(state, summary, learning), limit=6)
    return summarize_memory_payload(
        history=history,
        summary=summary,
        profile=profile,
        learning=learning,
        state=state,
    ) | {
        "recent_events": [_serialize_event(row) for row in events],
        "business": {
            "name": business.get("name"),
            "type": business.get("business_type"),
            "delivery_info": business.get("delivery_info"),
            "support_details": business.get("support_details"),
            "hours": business.get("hours"),
            "address": business.get("address"),
        },
        "semantic_context": summarize_for_prompt(semantic_rows),
        "goal_tracking": state.get("goal_tracking", {}),
        "feedback": state.get("feedback", {}),
        "style": style,
    }


def save_turn(phone: str, user_msg: str, bot_msg: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    append_history(phone, "user", user_msg, business_id)
    append_history(phone, "assistant", bot_msg, business_id)
    refresh_summary(phone, business_id)


def update_language(user: Dict[str, Any], message: str, business_id: str) -> str:
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


def persist_state(phone: str, business_id: str, state: Dict[str, Any], role: str) -> None:
    sanitized = {
        "business_id": business_id,
        "phone": phone,
        "role": role,
        "pending_action": state.get("pendingAction"),
        "last_language": state.get("lastLanguage", "english"),
        "last_message": state.get("lastMessage", ""),
        "workflow": state.get("workflow", {}),
        "cart": state.get("cart", {}),
        "customer_summary": state.get("customer_summary", ""),
        "intent_history": state.get("intent_history", [])[-20:],
        "uncertainty": state.get("uncertainty", {}),
        "last_order_id": state.get("last_order_id", ""),
        "last_vendor_order_id": state.get("last_vendor_order_id", ""),
        "open_ticket_id": state.get("open_ticket_id", ""),
        "goal_tracking": state.get("goal_tracking", {}),
        "feedback": state.get("feedback", {}),
        "last_reflection": state.get("last_reflection", {}),
        "last_tool_gap": state.get("last_tool_gap", {}),
        "memory_version": 3,
        "updated_at": utc_now(),
    }
    get_collection("conversation_state").update_one(
        {"business_id": business_id, "phone": phone},
        {"$set": sanitized},
        upsert=True,
    )


def note_intent(phone: str, business_id: str, state: Dict[str, Any], intent: str, confidence: float) -> None:
    state["intent_history"] = list(state.get("intent_history", [])) + [
        {"intent": intent, "confidence": round(confidence, 3), "at": utc_now().isoformat()}
    ]


def refresh_summary(phone: str, business_id: str) -> None:
    history = get_history(phone, business_id)
    messages = [item["content"] for item in history if item.get("content")]
    if not messages:
        return

    token_counter = Counter()
    for message in messages[-10:]:
        token_counter.update(tokenize(message))

    top_tokens = [token for token, _ in token_counter.most_common(8)]
    summary = " | ".join(messages[-4:])[:600]

    get_collection("conversation_summaries").update_one(
        {"business_id": business_id, "phone": phone},
        {
            "$set": {
                "summary": summary,
                "preferences": top_tokens[:5],
                "top_intents": _recent_user_topics(history),
                "salient_entities": {"keywords": top_tokens},
                "last_updated_at": utc_now(),
            }
        },
        upsert=True,
    )


def update_goal_tracking(
    state: Dict[str, Any],
    *,
    intent: str,
    user_message: str,
    decision: Dict[str, Any] | None = None,
    observation: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    tracking = dict(DEFAULT_STATE["goal_tracking"])
    tracking.update(state.get("goal_tracking") or {})
    if _should_reset_goal(state, tracking, intent, user_message):
        tracking = dict(DEFAULT_STATE["goal_tracking"])
    if not tracking.get("goal_text"):
        tracking["goal_type"] = intent
        tracking["goal_text"] = _goal_text(intent, user_message)
        tracking["status"] = "active"
    if decision and decision.get("goal"):
        tracking.update({key: value for key, value in decision["goal"].items() if value not in ("", None, [])})
    if decision and decision.get("plan_steps"):
        tracking["current_plan"] = decision["plan_steps"][:8]
    if observation and observation.get("ok"):
        completed = list(tracking.get("completed_steps") or [])
        action = str(observation.get("tool") or "")
        if action and action not in completed:
            completed.append(action)
        tracking["completed_steps"] = completed[-10:]
        plan = list(tracking.get("current_plan") or [])
        tracking["current_plan"] = [step for step in plan if str(step.get("action") or "") != action]
        total = len(completed) + len(tracking["current_plan"])
        tracking["progress_score"] = round(len(completed) / total, 4) if total else 1.0
    if intent in {"checkout", "order_status"} and state.get("last_order_id"):
        tracking["status"] = "completed"
    if (state.get("workflow") or {}).get("name") == "order_checkout" and tracking.get("status") not in {"completed", "cancelled"}:
        tracking["status"] = "active"
        if tracking.get("goal_type") not in {"cart", "checkout"}:
            tracking["goal_type"] = "checkout"
        if not tracking.get("goal_text"):
            tracking["goal_text"] = "Collect missing purchase details and place the order."
    if observation and observation.get("tool") in {"resolve_support_ticket"} and observation.get("ok"):
        tracking["status"] = "completed"
    if observation and observation.get("tool") == "checkout_order" and observation.get("ok"):
        tracking["status"] = "completed"
    tracking["last_updated_at"] = utc_now().isoformat()
    return tracking


def store_turn_memories(
    phone: str,
    business_id: str,
    *,
    user_message: str,
    bot_reply: str,
    state: Dict[str, Any],
    intent: str,
) -> None:
    remember(phone, business_id, kind="user_turn", text=user_message, metadata={"intent": intent})
    remember(phone, business_id, kind="assistant_turn", text=bot_reply, metadata={"intent": intent})
    goal_text = str((state.get("goal_tracking") or {}).get("goal_text") or "").strip()
    if goal_text:
        remember(phone, business_id, kind="goal", text=goal_text, metadata={"status": (state.get("goal_tracking") or {}).get("status", "")})


def _recent_user_topics(history: List[Dict[str, str]]) -> List[str]:
    tokens = []
    for row in history[-8:]:
        if row.get("role") != "user":
            continue
        tokens.extend(tokenize(row.get("content", "")))
    counter = Counter(tokens)
    return [token for token, _ in counter.most_common(6)]


def _serialize_event(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": document.get("kind"),
        "payload": document.get("payload", {}),
        "created_at": document.get("created_at").isoformat() if hasattr(document.get("created_at"), "isoformat") else document.get("created_at"),
    }


def _semantic_query(state: Dict[str, Any], summary: Dict[str, Any], learning: Dict[str, Any]) -> str:
    parts = [
        str((state.get("goal_tracking") or {}).get("goal_text") or ""),
        str(summary.get("summary") or ""),
        " ".join(summary.get("preferences", [])[:5]),
        " ".join(learning.get("preference_terms", [])[:6]),
    ]
    return " ".join(part for part in parts if part).strip()


def _goal_text(intent: str, user_message: str) -> str:
    templates = {
        "general": "Understand the customer's need before taking action.",
        "cart": "Understand demand and prepare the right cart.",
        "checkout": "Collect missing purchase details and place the order.",
        "order_status": "Resolve the order status request with certainty.",
        "support": "Resolve the customer's issue and restore confidence.",
        "browse": "Guide the customer toward the best next product choice.",
        "vendor_ops": "Help the vendor complete the operational request correctly.",
    }
    return templates.get(intent, user_message[:200] or "Assist the conversation effectively.")


def _should_reset_goal(state: Dict[str, Any], tracking: Dict[str, Any], intent: str, user_message: str) -> bool:
    if not tracking.get("goal_text"):
        return False
    if tracking.get("status") == "completed":
        return True
    lowered = str(user_message or "").lower().strip()
    if any(phrase in lowered for phrase in {"cancel", "cancel order", "stop order", "never mind", "forget it"}):
        return True
    if (state.get("workflow") or {}).get("name") == "order_checkout" or (state.get("cart") or {}).get("items") or tracking.get("current_plan"):
        return False
    current = str(tracking.get("goal_type") or "")
    if intent == "general":
        return False
    return bool(current and current != intent and tracking.get("status") in {"completed", "cancelled"})
