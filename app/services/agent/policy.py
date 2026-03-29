from __future__ import annotations

from typing import Any, Dict, List

from app.services.agent.learning import learned_token_weights, tokenize
from app.services.agent.tools import vendor_status_candidates


INTENT_PRIORS = {
    "support": {"refund", "issue", "problem", "wrong", "complaint", "late", "delay", "help", "damaged"},
    "order_status": {"status", "track", "where", "delivery", "arrive", "order"},
    "cart": {"buy", "want", "need", "add", "cart", "take", "pack", "piece", "quantity"},
    "checkout": {"checkout", "place", "confirm", "address", "deliver", "ship", "send"},
    "browse": {"show", "available", "options", "catalog", "menu", "products", "suggest"},
    "vendor_ops": {"product", "stock", "offer", "promotion", "broadcast", "business", "update"},
}


def analyze_message(
    message: str,
    *,
    user: Dict[str, Any],
    state: Dict[str, Any],
    learning_profile: Dict[str, Any],
) -> Dict[str, Any]:
    lowered = message.lower().strip()
    tokens = tokenize(lowered)
    scores = {intent: 0.0 for intent in INTENT_PRIORS}
    learned_weights = learned_token_weights(learning_profile)

    for intent, priors in INTENT_PRIORS.items():
        for token in tokens:
            if token in priors:
                scores[intent] += 0.3
            scores[intent] += learned_weights.get(token, 0.0) * 0.45

    if user.get("role") == "vendor":
        scores["vendor_ops"] += 0.22
        status_options = vendor_status_candidates(lowered, user.get("business_id", ""), learning_profile=learning_profile)
    else:
        status_options = []

    workflow = state.get("workflow", {})
    if workflow.get("stage") == "collect_address":
        scores["checkout"] += 0.24
    if workflow.get("stage") == "ready_to_checkout" and lowered in {"yes", "ok", "okay", "confirm", "place", "proceed"}:
        scores["checkout"] += 0.8
    if any(char.isdigit() for char in lowered):
        scores["cart"] += 0.14
    if "address" in lowered:
        scores["checkout"] += 0.22

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    intent, confidence = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0.0
    uncertainty = max(0.0, 0.62 - (confidence - runner_up))

    return {
        "intent": intent,
        "category": _category_for_intent(intent),
        "confidence": round(min(0.98, confidence + 0.14), 3),
        "uncertainty": round(min(1.0, uncertainty), 3),
        "tokens": tokens,
        "signals": ordered,
        "vendor_status_options": status_options,
        "complexity": _complexity(message, state, user, status_options),
        "needs_llm": _needs_llm(message, state, user, confidence, runner_up, status_options),
    }


def build_strategy(
    *,
    user: Dict[str, Any],
    profile: Dict[str, Any],
    analysis: Dict[str, Any],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    segment = profile.get("segment", "new")
    mode = analysis.get("category", "sales")
    support_open = bool(state.get("open_ticket_id"))
    cart_size = len((state.get("cart") or {}).get("items", []))
    service_level = "premium" if segment == "vip" else "standard"

    strategy = {
        "mode": mode,
        "goal": "assist",
        "priority": "high" if service_level == "premium" else "normal",
        "upsell_allowed": mode == "sales" and not support_open,
        "support_guard": support_open or mode == "support",
        "workflow_bias": state.get("workflow", {}).get("name") or "",
        "service_level": service_level,
        "autonomy_level": "high" if analysis.get("complexity") != "trivial" else "medium",
        "personalization_weight": 0.72 if segment in {"repeat", "vip"} else 0.48,
        "exploration": 0.1 if segment in {"new", "at_risk"} else 0.04,
        "response_style": "empathetic" if mode == "support" else ("premium" if service_level == "premium" else "direct"),
        "planning_horizon": 3 if analysis.get("complexity") in {"high", "multi_step"} else 2,
        "goal_persistence": bool((state.get("goal_tracking") or {}).get("goal_text")),
        "feedback_bias": (state.get("feedback") or {}).get("sentiment", "neutral"),
    }

    if mode == "support":
        strategy["goal"] = "resolve_issue_fast"
        strategy["upsell_allowed"] = False
    elif mode == "order":
        strategy["goal"] = "complete_order_or_status_request"
        if cart_size == 0:
            strategy["upsell_allowed"] = False
    else:
        strategy["goal"] = "increase_conversion_without_friction"

    return strategy


def select_cost_tier(analysis: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any]) -> str:
    if analysis.get("complexity") in {"high", "multi_step"}:
        return "smart"
    if (state.get("goal_tracking") or {}).get("current_plan"):
        return "balanced"
    if analysis.get("needs_llm"):
        return "balanced"
    if analysis.get("uncertainty", 1.0) <= 0.12 and not state.get("open_ticket_id"):
        return "cheap"
    return "balanced"


def cheap_action_allowed(analysis: Dict[str, Any], user: Dict[str, Any], state: Dict[str, Any]) -> bool:
    if (state.get("goal_tracking") or {}).get("current_plan"):
        return False
    if user.get("role") == "vendor" and analysis.get("vendor_status_options") and analysis["vendor_status_options"][0]["score"] >= 1.4:
        return True
    if state.get("workflow", {}).get("stage") == "collect_address" and len(analysis.get("tokens", [])) >= 3 and analysis.get("intent") == "checkout":
        return True
    if state.get("workflow", {}).get("stage") == "ready_to_checkout" and analysis.get("intent") == "checkout" and analysis.get("confidence", 0.0) >= 0.75:
        return True
    return False


def _needs_llm(
    message: str,
    state: Dict[str, Any],
    user: Dict[str, Any],
    confidence: float,
    runner_up: float,
    status_options: List[Dict[str, Any]],
) -> bool:
    if user.get("role") == "vendor" and status_options and status_options[0]["score"] >= 1.4:
        return False
    if state.get("workflow", {}).get("stage") == "collect_address" and len(message.split()) >= 3:
        return False
    if state.get("workflow", {}).get("stage") == "ready_to_checkout" and message.strip().lower() in {"yes", "ok", "okay", "confirm", "place", "proceed"}:
        return False
    return not (confidence >= 0.9 and confidence - runner_up >= 0.32 and len(message.split()) <= 2)


def _complexity(message: str, state: Dict[str, Any], user: Dict[str, Any], status_options: List[Dict[str, Any]]) -> str:
    if user.get("role") == "vendor" and status_options and status_options[0]["score"] >= 1.4:
        return "trivial"
    if state.get("open_ticket_id") or state.get("workflow", {}).get("name"):
        return "multi_step"
    if len(message.split()) >= 18:
        return "high"
    if len(message.split()) <= 3:
        return "trivial"
    return "normal"


def _category_for_intent(intent: str) -> str:
    if intent == "support":
        return "support"
    if intent in {"order_status", "checkout", "cart"}:
        return "order"
    return "sales"
