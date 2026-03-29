import json
from typing import Any, Dict, List


def build_planner_messages(
    *,
    user_message: str,
    user: Dict[str, Any],
    memory: Dict[str, Any],
    tools: List[Dict[str, Any]],
    scratchpad: List[Dict[str, Any]],
    strategy: Dict[str, Any],
    analysis: Dict[str, Any],
) -> List[Dict[str, str]]:
    system = {
        "role": "system",
        "content": (
            "You are an autonomous WhatsApp business agent.\n"
            "You handle sales, order management, and customer support.\n"
            "Be outcome-driven, concise, and realistic.\n"
            "Prefer using tools over inventing facts.\n"
            "Use internal investigation tools first when context is missing, then take customer-facing action.\n"
            "When confidence is low, ask one focused question.\n"
            "When a tool fails, adapt using the error and do not repeat the same failing call.\n"
            "For support, resolve the issue before selling.\n"
            "For vendors, you may manage catalog, status updates, offers, broadcasts, and support.\n"
            "You are allowed to think strategically about service recovery, conversion, personalization, and cost.\n"
            "Do not default to a scripted flow if the available context supports a better next step.\n"
            "Maintain long-horizon goals across turns. Reuse the active goal and update the plan instead of reacting only to the latest message.\n"
            "If the conversation already has a plan, continue it unless new evidence invalidates it.\n"
            "Use semantic memory, past feedback, and recent failures before deciding.\n"
            "When uncertain, reflect that uncertainty explicitly and prefer investigation or one focused clarification.\n"
            "Customer-facing message MUST follow the requested chat style exactly: english, manglish, or malayalam.\n"
            "Respond with one JSON object only:\n"
            "{"
            '"thought":"...",'
            '"action":"tool_name_or_respond",'
            '"arguments":{},'
            '"message":"customer facing reply when action is respond or when tool needs framing",'
            '"confidence":0.0,'
            '"uncertainty":0.0,'
            '"goal":{"goal_type":"...", "goal_text":"...", "status":"active"},'
            '"plan_steps":[{"action":"tool_name","arguments":{},"reason":"..."}]'
            "}"
        ),
    }

    context = {
        "role": "user",
        "content": json.dumps(
            {
                "user": {
                    "role": user.get("role"),
                    "phone": user.get("phone"),
                    "business_id": user.get("business_id"),
                },
                "strategy": strategy,
                "analysis": analysis,
                "memory": memory,
                "available_tools": tools,
                "tool_shortlist": [tool["name"] for tool in tools[:8]],
                "scratchpad": scratchpad,
                "latest_user_message": user_message,
                "requested_style": memory.get("style", "english"),
            },
            ensure_ascii=False,
        ),
    }
    return [system, context]


def summarize_memory_payload(
    *,
    history: List[Dict[str, str]],
    summary: Dict[str, Any],
    profile: Dict[str, Any],
    learning: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    compact_history = history[-8:]
    return {
        "summary": summary,
        "profile": {
            "segment": profile.get("segment", "unknown"),
            "rfm": profile.get("rfm", {}),
            "favorite_products": profile.get("favorite_products", [])[:5],
            "recent_intents": profile.get("recent_intents", [])[-5:],
            "support_risk": profile.get("support_risk", 0.0),
        },
        "learning": {
            "last_intent": learning.get("last_intent"),
            "preference_terms": learning.get("preference_terms", [])[:10],
            "top_product_interest": sorted(
                (learning.get("product_interest") or {}).items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5],
        },
        "state": {
            "workflow": state.get("workflow"),
            "cart": state.get("cart", {}),
            "open_ticket_id": state.get("open_ticket_id"),
            "last_order_id": state.get("last_order_id"),
            "last_vendor_order_id": state.get("last_vendor_order_id"),
            "uncertainty": state.get("uncertainty", {}),
            "goal_tracking": state.get("goal_tracking", {}),
            "feedback": state.get("feedback", {}),
        },
        "history": compact_history,
    }
