from __future__ import annotations

import re
from typing import Any, Dict, List

from app.services.agent.extraction import (
    message_has_multi_entity_cart_input,
    message_has_structured_cart_payload,
    message_requires_cart_extraction,
)
from app.services.agent.learning import learned_token_weights, rank_token_match, tokenize
from app.services.agent.tools import vendor_status_candidates


INTENT_PRIORS = {
    "general": {"hi", "hello", "hey", "hii", "yo", "hola", "greetings"},
    "support": {"refund", "issue", "problem", "wrong", "complaint", "late", "delay", "help", "damaged"},
    "order_status": {"status", "track", "where", "delivery", "arrive", "shipped", "pending", "delivered"},
    "cart": {"buy", "want", "need", "add", "cart", "take", "pack", "piece", "quantity", "order"},
    "checkout": {"checkout", "place", "confirm", "address", "deliver", "ship", "send"},
    "browse": {"show", "available", "options", "catalog", "menu", "products", "suggest", "new", "make", "start"},
    "vendor_ops": {"product", "stock", "offer", "promotion", "broadcast", "business", "update"},
}

BROWSE_MARKERS = {
    "show",
    "see",
    "browse",
    "catalog",
    "menu",
    "products",
    "product",
    "available",
    "options",
    "kaanik",
    "kanik",
    "undo",
    "entha",
}

INTENT_PROTOTYPES = {
    "browse": [
        "show me your products",
        "what products do you have",
        "i want to browse the catalog",
        "available products and options",
        "eth products aan ullath",
        "products undo",
        "catalog kaanikk",
    ],
    "cart": [
        "add this to cart",
        "i want to buy two items",
        "need 2 pieces",
        "place order for one product",
    ],
    "order_status": [
        "where is my order",
        "track my order",
        "order status update",
        "when will delivery arrive",
    ],
    "support": [
        "i have a problem with my order",
        "need help with refund",
        "this item is damaged",
        "customer support needed",
        "my order is late and i need help",
        "refund for wrong item",
        "replace damaged product",
    ],
}

CORRECTION_PHRASES = {
    "not my address",
    "wrong address",
    "not the address",
    "that is not my address",
    "thats not my address",
    "that's not my address",
    "wrong",
    "incorrect",
    "change address",
    "update address",
    "not that",
}

ACKNOWLEDGEMENT_TOKENS = {"yeah", "yes", "ok", "okay", "show", "it", "bill", "fine", "sure", "hmm"}
CANCEL_PHRASES = {"cancel", "cancel order", "stop order", "never mind", "forget it"}
ORDER_STATUS_PHRASES = {"my order", "order status", "where is my order", "track order", "track my order", "see my order", "view my order"}


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
    prototype_scores = _intent_prototype_scores(lowered, learning_profile)
    for intent, score in prototype_scores.items():
        scores[intent] += score * 0.55

    if user.get("role") == "vendor":
        scores["vendor_ops"] += 0.22
        status_options = vendor_status_candidates(lowered, user.get("business_id", ""), learning_profile=learning_profile)
    else:
        status_options = []

    workflow = state.get("workflow", {})
    cart_items = list((state.get("cart") or {}).get("items", []))
    correction = _is_correction(lowered)
    probable_address = _looks_like_address(message)
    structured_cart_payload = message_has_structured_cart_payload(message)
    multi_entity_cart = message_has_multi_entity_cart_input(message)
    cart_extraction_required = message_requires_cart_extraction(message)
    cart_view_request = _is_cart_view_request(lowered)
    explicit_cancel = any(phrase in lowered for phrase in CANCEL_PHRASES)
    if workflow.get("stage") == "collect_address":
        scores["checkout"] += 0.24
        if correction:
            scores["checkout"] += 0.25
    if workflow.get("name") == "order_checkout":
        scores["cart"] += 0.18
        scores["checkout"] += 0.22
    if cart_items:
        scores["cart"] += 0.18
        scores["checkout"] += 0.16
        if cart_view_request:
            scores["cart"] += 0.5
            scores["browse"] -= 0.35
    if workflow.get("stage") == "ready_to_checkout" and lowered in {"yes", "ok", "okay", "confirm", "place", "proceed"}:
        scores["checkout"] += 0.8
    if "new order" in lowered or "make a new order" in lowered or "place an order" in lowered:
        scores["cart"] += 0.55
        scores["browse"] += 0.2
        scores["order_status"] -= 0.2
    if any(phrase in lowered for phrase in ORDER_STATUS_PHRASES):
        scores["order_status"] += 0.75
        scores["browse"] -= 0.25
        scores["support"] -= 0.1
    if any(term in lowered for term in {"refund", "replace", "damaged", "issue", "problem", "wrong item", "late order", "missing item"}):
        scores["support"] += 0.48
        scores["browse"] -= 0.18
    if "order" in tokens and any(token in tokens for token in {"see", "view", "track", "status", "where"}):
        scores["order_status"] += 0.34
        scores["browse"] -= 0.12
    if any(char.isdigit() for char in lowered):
        scores["cart"] += 0.14
    if structured_cart_payload or cart_extraction_required:
        scores["cart"] += 0.42
        scores["general"] -= 0.12
    if multi_entity_cart:
        scores["cart"] += 0.18
    if "address" in lowered:
        scores["checkout"] += 0.22
    if lowered in {"hi", "hello", "hey", "hey there", "hey theree", "hii"}:
        scores["general"] += 0.9
    if _is_stretched_greeting(lowered):
        scores["general"] += 0.9
    if _looks_like_browse_request(lowered, tokens):
        scores["browse"] += 0.82
        scores["general"] -= 0.12
        if "show" in tokens or "see" in tokens or "all" in tokens:
            scores["cart"] -= 0.16
    if _browse_intent_strength(tokens) >= 2:
        scores["browse"] += 0.28
        if not any(token.isdigit() for token in tokens):
            scores["cart"] -= 0.18
    if "all the products" in lowered or "see all the products" in lowered or "see what you have" in lowered:
        scores["browse"] += 0.45
        scores["cart"] -= 0.2
    active_goal = str((state.get("goal_tracking") or {}).get("goal_type") or "").strip()
    if active_goal == "browse" and not workflow.get("name"):
        if intent_like_browse := _looks_like_browse_request(lowered, tokens):
            scores["browse"] += 0.22
        elif len(tokens) <= 4 and not correction:
            scores["browse"] += 0.08

    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    intent, confidence = ordered[0]
    runner_up = ordered[1][1] if len(ordered) > 1 else 0.0
    if confidence <= 0.12:
        intent = "general"
        runner_up = ordered[0][1] if ordered else 0.0
    margin = max(0.0, confidence - runner_up)
    calibrated_confidence = _calibrate_confidence(intent, confidence, runner_up, prototype_scores.get(intent, 0.0))
    uncertainty = _calibrate_uncertainty(intent, confidence, runner_up, prototype_scores.get(intent, 0.0))

    return {
        "intent": intent,
        "category": _category_for_intent(intent),
        "confidence": round(calibrated_confidence, 3),
        "uncertainty": round(min(1.0, uncertainty), 3),
        "tokens": tokens,
        "signals": ordered,
        "vendor_status_options": status_options,
        "is_correction": correction,
        "probable_address": probable_address,
        "intent_margin": round(margin, 3),
        "prototype_score": round(prototype_scores.get(intent, 0.0), 3),
        "browse_strength": _browse_intent_strength(tokens),
        "complexity": _complexity(message, state, user, status_options),
        "needs_llm": _needs_llm(message, state, user, confidence, runner_up, status_options, probable_address, correction),
        "structured_cart_payload": structured_cart_payload,
        "multi_entity_cart": multi_entity_cart,
        "cart_extraction_required": cart_extraction_required,
        "cart_view_request": cart_view_request,
        "explicit_cancel": explicit_cancel,
        "order_workflow_locked": workflow.get("name") == "order_checkout" and not explicit_cancel,
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
        "customer_context_priority": 0.7 if segment in {"repeat", "vip"} else 0.45,
        "support_recovery_priority": 0.88 if support_open or mode == "support" else 0.2,
        "business_context_priority": 0.55 if mode in {"sales", "support"} else 0.3,
        "memory_priority": 0.66 if analysis.get("complexity") in {"high", "multi_step"} or support_open else 0.3,
    }

    if mode == "support":
        strategy["goal"] = "resolve_issue_fast"
        strategy["upsell_allowed"] = False
    elif mode == "order":
        strategy["goal"] = "complete_order_or_status_request"
        if cart_size == 0:
            strategy["upsell_allowed"] = False
    elif analysis.get("intent") == "general":
        strategy["goal"] = "engage_and_discover_need"
    else:
        strategy["goal"] = "increase_conversion_without_friction"

    return strategy


def select_cost_tier(analysis: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any]) -> str:
    if cheap_action_allowed(analysis, user, state):
        return "cheap"
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
    if state.get("last_tool_gap"):
        return False
    if analysis.get("intent") in {"cart", "checkout"}:
        return False
    if analysis.get("cart_extraction_required") or analysis.get("structured_cart_payload") or analysis.get("multi_entity_cart"):
        return False
    if analysis.get("order_workflow_locked"):
        return False
    if (state.get("cart") or {}).get("items"):
        return False
    if analysis.get("intent") == "general" and analysis.get("confidence", 0.0) >= 0.52 and analysis.get("uncertainty", 1.0) <= 0.42 and analysis.get("prototype_score", 0.0) >= 0.18 and not state.get("workflow", {}).get("name"):
        return True
    if analysis.get("intent") == "browse" and analysis.get("confidence", 0.0) >= 0.58 and analysis.get("uncertainty", 1.0) <= 0.36 and (analysis.get("browse_strength", 0) >= 2 or analysis.get("prototype_score", 0.0) >= 0.45) and not state.get("workflow", {}).get("name"):
        return True
    if user.get("role") == "vendor" and analysis.get("vendor_status_options") and analysis["vendor_status_options"][0]["score"] >= 1.4:
        return True
    if state.get("workflow", {}).get("stage") == "collect_address" and analysis.get("intent") == "checkout" and analysis.get("probable_address") and not analysis.get("is_correction"):
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
    probable_address: bool,
    correction: bool,
) -> bool:
    if user.get("role") == "vendor" and status_options and status_options[0]["score"] >= 1.4:
        return False
    if correction:
        return True
    if state.get("workflow", {}).get("stage") == "collect_address" and probable_address:
        return False
    if state.get("workflow", {}).get("stage") == "ready_to_checkout" and message.strip().lower() in {"yes", "ok", "okay", "confirm", "place", "proceed"}:
        return False
    return not (confidence >= 0.9 and confidence - runner_up >= 0.32 and len(message.split()) <= 2)


def _complexity(message: str, state: Dict[str, Any], user: Dict[str, Any], status_options: List[Dict[str, Any]]) -> str:
    if user.get("role") == "vendor" and status_options and status_options[0]["score"] >= 1.4:
        return "trivial"
    if state.get("open_ticket_id") or state.get("workflow", {}).get("name"):
        return "multi_step"
    if message_requires_cart_extraction(message):
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


def _is_correction(lowered: str) -> bool:
    return any(phrase in lowered for phrase in CORRECTION_PHRASES)


def _looks_like_address(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return False
    if _is_correction(lowered):
        return False
    tokens = [token for token in lowered.replace(",", " ").split() if token]
    if len(tokens) < 4:
        return False
    if len(tokens) <= 5 and sum(1 for token in tokens if token in ACKNOWLEDGEMENT_TOKENS) >= 2:
        return False
    address_keywords = {"road", "rd", "street", "st", "lane", "house", "building", "floor", "flat", "near", "pin", "city", "district", "post", "po", "junction"}
    has_keyword = any(token in address_keywords for token in tokens)
    has_number = any(any(ch.isdigit() for ch in token) for token in tokens)
    has_separator = "," in message or "-" in message or "/" in message
    return has_keyword or (has_number and (has_separator or len(tokens) >= 5))


def _is_stretched_greeting(lowered: str) -> bool:
    return bool(re.fullmatch(r"(h+i+|he+y+|hey+y+|hello+)", lowered))


def _is_cart_view_request(lowered: str) -> bool:
    return lowered in {"show my cart", "show cart", "view cart", "my cart", "cart"} or ("cart" in lowered and any(token in lowered for token in {"show", "view", "what", "inside"}))


def _looks_like_browse_request(lowered: str, tokens: List[str]) -> bool:
    if not lowered:
        return False
    phrases = {
        "what products do you have",
        "what do you have",
        "what all do you have",
        "i want to see products",
        "i want to see the products",
        "show me what products you have",
        "show me what you have",
        "show products",
        "show me products",
        "i want to see all the products",
        "i want to see the products",
        "show all products",
        "see all the products",
        "available products",
        "product list",
        "catalog",
        "menu",
    }
    if lowered in phrases:
        return True
    if ("products" in tokens or "product" in tokens) and any(word in tokens for word in {"show", "see", "browse"}):
        return True
    if "what" in tokens and any(word in tokens for word in {"have", "available"}):
        return True
    if "products" in tokens and any(word in tokens for word in {"show", "see", "available", "all"}):
        return True
    if "what do you have" in lowered or "products do you have" in lowered:
        return True
    if _browse_intent_strength(tokens) >= 2:
        return True
    return False


def _browse_intent_strength(tokens: List[str]) -> int:
    score = 0
    token_set = set(tokens)
    if token_set & {"show", "see", "browse", "catalog", "menu", "kaanik", "kanik"}:
        score += 1
    if token_set & {"products", "product", "available", "options", "undo"}:
        score += 1
    if token_set & {"want", "need", "entha"}:
        score += 1
    return score


def _intent_prototype_scores(lowered: str, learning_profile: Dict[str, Any]) -> Dict[str, float]:
    results: Dict[str, float] = {}
    for intent, prototypes in INTENT_PROTOTYPES.items():
        ranked = rank_token_match(lowered, prototypes, learned_weights=learned_token_weights(learning_profile, intent=intent))
        results[intent] = float(ranked[0]["score"]) if ranked else 0.0
    return results


def _calibrate_confidence(intent: str, top_score: float, runner_up: float, prototype_score: float) -> float:
    margin = max(0.0, top_score - runner_up)
    confidence = 0.22 + top_score * 0.34 + margin * 0.9 + prototype_score * 0.24
    if intent in {"general", "browse"}:
        confidence = min(confidence, 0.86)
    return max(0.18, min(confidence, 0.94))


def _calibrate_uncertainty(intent: str, top_score: float, runner_up: float, prototype_score: float) -> float:
    margin = max(0.0, top_score - runner_up)
    uncertainty = 0.74 - margin * 0.72 - prototype_score * 0.28
    if intent == "browse":
        uncertainty = max(0.14, uncertainty)
    elif intent == "general":
        uncertainty = max(0.18, uncertainty)
    else:
        uncertainty = max(0.08, uncertainty)
    return uncertainty
