from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services.agent.localization import style_text
from app.services.agent.policy import cheap_action_allowed
from app.services.agent.prompts import build_planner_messages
from app.services.agent.tools import rank_tools
from app.services.agent.types import PlannerDecision, ToolObservation
from app.services.llm import call_agent_llm


def _log_planner(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[agent.planner] {event} {normalized}".rstrip())


def choose_next_action(
    *,
    user_message: str,
    user: Dict[str, Any],
    state: Dict[str, Any],
    memory_payload: Dict[str, Any],
    analysis: Dict[str, Any],
    strategy: Dict[str, Any],
    learning_profile: Dict[str, Any],
    scratchpad: List[ToolObservation],
    max_steps: int,
) -> PlannerDecision:
    safety_action = route_by_intent(analysis.get("intent", ""), user_message, {"workflow": state.get("workflow") or {}, "cart": state.get("cart") or {}, "analysis": analysis})
    if safety_action:
        return _decision_for_routed_action(safety_action, user_message, state, analysis)

    if should_interrupt_workflow(state.get("workflow") or {}, analysis.get("intent", ""), user_message):
        state["workflow"] = {"name": "", "stage": "", "required_slots": [], "filled_slots": {}, "next_question": ""}
        goal_tracking = dict(state.get("goal_tracking") or {})
        goal_tracking["current_plan"] = []
        state["goal_tracking"] = goal_tracking

    persisted = _planned_decision_from_state(user_message, state, scratchpad, analysis)

    tool_ranking = rank_tools(
        user=user,
        state=state,
        analysis=analysis,
        strategy=strategy,
        learning_profile=learning_profile,
        scratchpad=scratchpad,
    )

    if cheap_action_allowed(analysis, user, state):
        _log_planner("cheap_action_allowed", intent=analysis.get("intent"), workflow=(state.get("workflow") or {}).get("stage"))
        return _micro_decision(user_message, user, state, analysis)

    if state.get("force_deterministic_fallback") or int(state.get("llm_calls_this_run", 0)) >= 1:
        return decide_action_without_llm(analysis.get("intent", "general"), state.get("workflow") or {}, user_message, state=state, analysis=analysis)

    messages = build_planner_messages(
        user_message=user_message,
        user=user,
        memory=memory_payload,
        tools=tool_ranking[:10],
        scratchpad=[_observation_to_prompt(row) for row in scratchpad[-max_steps:]],
        strategy=strategy | {"tool_shortlist": [item["name"] for item in tool_ranking[:6]], "soft_plan_bias": persisted.arguments if persisted else {}, "soft_plan_action": persisted.action if persisted else ""},
        analysis=analysis,
    )
    try:
        state["llm_calls_this_run"] = int(state.get("llm_calls_this_run", 0)) + 1
        _log_planner("llm_planner_start", tier=strategy.get("llm_tier", "balanced"), top_tools=[item["name"] for item in tool_ranking[:5]], uncertainty=analysis.get("uncertainty"))
        payload = call_agent_llm(messages, tier=strategy.get("llm_tier", "balanced"))
    except Exception:
        _log_planner("llm_planner_failed", tier=strategy.get("llm_tier", "balanced"))
        return _decision_for_routed_action(fallback_by_intent(analysis.get("intent", "general")), user_message, state, analysis)

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return _decision_for_routed_action(fallback_by_intent(analysis.get("intent", "general")), user_message, state, analysis)

    decision = PlannerDecision(
        action=str(payload.get("action") or "respond").strip() or "respond",
        arguments=dict(payload.get("arguments") or {}),
        message=str(payload.get("message") or "").strip(),
        rationale=str(payload.get("thought") or "").strip(),
        confidence=float(payload.get("confidence") or 0.45),
        plan_steps=list(payload.get("plan_steps") or []),
        goal=dict(payload.get("goal") or {}),
        uncertainty=float(payload.get("uncertainty") or analysis.get("uncertainty") or 0.0),
    )
    decision = _enforce_workflow_lock(decision, user_message, state, analysis)
    _log_planner("llm_planner_result", action=decision.action, confidence=decision.confidence, rationale=decision.rationale, arguments=decision.arguments)
    return decision


def _micro_decision(user_message: str, user: Dict[str, Any], state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision:
    lowered = user_message.strip().lower()
    style = state.get("lastLanguage", "english")
    workflow = state.get("workflow") or {}

    if user.get("role") == "vendor" and analysis.get("vendor_status_options"):
        return PlannerDecision(action="vendor_update_order_status", arguments={"status": analysis["vendor_status_options"][0]["status"], "raw_message": user_message}, rationale="strong_vendor_status_signal", confidence=0.84)
    if analysis.get("explicit_cancel"):
        return PlannerDecision(action="respond", message=style_text(style, "Okay. I stopped the current order flow.", "Sheri. Ippo ulla order flow njan nirthi.", "Sheri. Ippo ulla order flow njan nirthi."), rationale="explicit_cancel", confidence=0.86)
    if analysis.get("is_correction") and (workflow.get("name") == "order_checkout" or (state.get("cart") or {}).get("items")):
        return PlannerDecision(action="repair_order_context", arguments={"raw_message": user_message}, rationale="order_flow_correction_shortcut", confidence=0.8)
    if analysis.get("cart_view_request") and (state.get("cart") or {}).get("items"):
        return PlannerDecision(action="manage_cart", arguments={"operation": "view", "raw_message": user_message}, rationale="cart_view_shortcut", confidence=0.88)
    if workflow.get("name") == "order_checkout":
        if workflow.get("stage") == "collect_address" and analysis.get("probable_address"):
            return PlannerDecision(action="collect_address", arguments={"address": user_message, "raw_message": user_message}, rationale="address_capture_shortcut", confidence=0.84)
        if analysis.get("intent") in {"cart", "checkout"} or analysis.get("cart_extraction_required"):
            return PlannerDecision(action="manage_cart", arguments={"query": user_message, "raw_message": user_message}, rationale="locked_order_cart_progress", confidence=0.72)
    if analysis.get("intent") == "browse" and _looks_like_catalog_browse(lowered) and not (state.get("cart") or {}).get("items"):
        return PlannerDecision(action="search_catalog", arguments={"query": user_message, "raw_message": user_message}, rationale="cheap_browse_shortcut", confidence=0.68)
    if analysis.get("intent") == "general":
        active_goal = str((state.get("goal_tracking") or {}).get("goal_type") or "").strip()
        if active_goal == "browse":
            return PlannerDecision(
                action="respond",
                message=style_text(style, "Hi. I can show you products. Tell me what type you want, or ask me to show the catalog.", "Hi. Njan products kaanikkam. Ethu type aanu venam enn parayu, allenkil catalog kaanikkan parayu.", "Hi. Njan products kaanikkam. Ethu type aanu venam enn parayu, allenkil catalog kaanikkan parayu."),
                rationale="cheap_general_browse_reengagement",
                confidence=0.62,
            )
        return PlannerDecision(
            action="respond",
            message=style_text(style, "Hi. Tell me what you want to do: browse products, place an order, track an order, or get help.", "Hi. Ningalkku browse cheyyano, order cheyyano, order track cheyyano, allenkil help veeno ennu parayu.", "Hi. Ningalkku browse cheyyano, order cheyyano, order track cheyyano, allenkil help veeno ennu parayu."),
            rationale="cheap_general_shortcut",
            confidence=0.58,
        )
    if workflow.get("stage") == "ready_to_checkout" and lowered in {"yes", "ok", "okay", "confirm", "place", "proceed"}:
        return PlannerDecision(action="checkout_order", arguments={"raw_message": user_message}, rationale="checkout_confirmation_shortcut", confidence=0.86)
    if analysis.get("intent") == "browse":
        return PlannerDecision(
            action="respond",
            message=style_text(style, "Tell me what kind of products you want, or ask me to show the full catalog.", "Ethu type products aanu venam enn parayu, allenkil full catalog kaanikkan parayu.", "Ethu type products aanu venam enn parayu, allenkil full catalog kaanikkan parayu."),
            rationale="browse_specific_prompt",
            confidence=0.48,
        )
    return PlannerDecision(action="respond", message=style_text(style, "Tell me what you need help with, and I will handle it.", "Entha sahayam venam enn parayu, njan handle cheyyam.", "Entha sahayam venam enn parayu, njan handle cheyyam."), rationale="micro_fallback", confidence=0.28)


def _ranked_fallback(user_message: str, tool_ranking: List[Dict[str, Any]], state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision:
    return _decision_for_routed_action(fallback_by_intent(analysis.get("intent", "general")), user_message, state, analysis)


def _safe_fallback_response(user_message: str, state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision | None:
    return _decision_for_routed_action(fallback_by_intent(analysis.get("intent", "general")), user_message, state, analysis)


def _planned_decision_from_state(user_message: str, state: Dict[str, Any], scratchpad: List[ToolObservation], analysis: Dict[str, Any]) -> PlannerDecision | None:
    goal_tracking = state.get("goal_tracking") or {}
    plan_steps = list(goal_tracking.get("current_plan") or [])
    if not plan_steps:
        return None
    recent_failures = {row.tool for row in scratchpad[-3:] if not row.ok}
    for step in plan_steps:
        action = str(step.get("action") or "").strip()
        if not action or action in recent_failures:
            continue
        decision = PlannerDecision(
            action=action,
            arguments=dict(step.get("arguments") or {}),
            rationale=str(step.get("reason") or "persisted_plan_step"),
            confidence=0.74,
            plan_steps=plan_steps,
            goal=dict(goal_tracking),
            uncertainty=0.18,
        )
        if not _should_override_persisted_step(decision, analysis):
            return decision
    return None


def _should_override_persisted_step(decision: PlannerDecision, analysis: Dict[str, Any]) -> bool:
    if analysis.get("explicit_cancel"):
        return True
    if analysis.get("intent") == "cart" and decision.action == "collect_address":
        return True
    if analysis.get("is_correction") and decision.action in {"collect_address", "checkout_order"}:
        return True
    if decision.action == "collect_address" and not analysis.get("probable_address"):
        return True
    if analysis.get("cart_view_request") and decision.action != "manage_cart":
        return True
    return False


def _enforce_workflow_lock(decision: PlannerDecision, user_message: str, state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision:
    if analysis.get("cart_view_request") and decision.action != "manage_cart":
        return _decision_for_routed_action("manage_cart", user_message, state, analysis, operation="view")
    if int((state.get("tool_failures") or {}).get(decision.action, 0)) >= 2:
        return _decision_for_routed_action(fallback_by_intent(analysis.get("intent", "general")), user_message, state, analysis)
    return decision


def should_interrupt_workflow(current_workflow: Dict[str, Any], intent: str, message: str) -> bool:
    if not current_workflow or not current_workflow.get("name"):
        return False
    lowered = str(message or "").lower()
    if intent == "order_status":
        return True
    if intent == "browse" or "product" in lowered or "catalog" in lowered:
        return True
    if current_workflow.get("stage") == "collect_address" and intent == "cart":
        return True
    return False


def route_by_intent(intent: str, message: str, context: Dict[str, Any]) -> str | None:
    lowered = str(message or "").strip().lower()
    workflow = context.get("workflow") or {}
    if intent == "browse":
        return "search_catalog"
    if intent == "order_status":
        return "get_order_status"
    if intent == "checkout" and lowered in {"confirm", "yes", "place order"} and workflow.get("stage") == "ready_to_checkout":
        return "checkout_order"
    if intent == "cart":
        return "manage_cart"
    if intent == "support":
        return "inspect_support_context"
    return None


def fallback_by_intent(intent: str) -> str:
    mapping = {
        "browse": "search_catalog",
        "cart": "manage_cart",
        "checkout": "collect_address",
        "order_status": "get_order_status",
        "support": "inspect_support_context",
    }
    return mapping.get(intent, "respond")


def decide_action_without_llm(intent: str, workflow: Dict[str, Any], message: str, *, state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision:
    return _decision_for_routed_action(fallback_by_intent(intent), message, state, analysis)


def _decision_for_routed_action(action: str, message: str, state: Dict[str, Any], analysis: Dict[str, Any], operation: str = "") -> PlannerDecision:
    lowered = str(message or "").strip().lower()
    if action == "search_catalog":
        return PlannerDecision(action="search_catalog", arguments={"query": message, "raw_message": message}, rationale="intent_safety_route", confidence=0.86)
    if action == "manage_cart":
        arguments: Dict[str, Any] = {"raw_message": message}
        if operation:
            arguments["operation"] = operation
        elif analysis.get("cart_view_request"):
            arguments["operation"] = "view"
        else:
            arguments["query"] = message
        return PlannerDecision(action="manage_cart", arguments=arguments, rationale="intent_safety_route", confidence=0.86)
    if action == "get_order_status":
        return PlannerDecision(action="get_order_status", arguments={"raw_message": message}, rationale="intent_safety_route", confidence=0.86)
    if action == "checkout_order":
        return PlannerDecision(action="checkout_order", arguments={"raw_message": message}, rationale="intent_safety_route", confidence=0.9)
    if action == "collect_address":
        if lowered in {"confirm", "yes", "place order"} and (state.get("workflow") or {}).get("stage") == "ready_to_checkout":
            return PlannerDecision(action="checkout_order", arguments={"raw_message": message}, rationale="confirm_checkout_route", confidence=0.92)
        return PlannerDecision(action="collect_address", arguments={"address": message, "raw_message": message}, rationale="intent_fallback_route", confidence=0.78)
    if action == "inspect_support_context":
        return PlannerDecision(action="inspect_support_context", arguments={"raw_message": message}, rationale="intent_safety_route", confidence=0.8)
    return PlannerDecision(
        action="respond",
        message=style_text(state.get("lastLanguage", "english"), "Tell me what you need help with, and I will handle it.", "Entha sahayam venam enn parayu, njan handle cheyyam.", "Entha sahayam venam enn parayu, njan handle cheyyam."),
        rationale="intent_fallback_respond",
        confidence=0.4,
    )


def _observation_to_prompt(observation: ToolObservation) -> Dict[str, Any]:
    return {
        "tool": observation.tool,
        "ok": observation.ok,
        "message": observation.message,
        "error": observation.error,
        "data": observation.data,
        "internal": observation.internal,
    }


def _looks_like_catalog_browse(lowered: str) -> bool:
    if not lowered:
        return False
    generic_phrases = {
        "what products do you have",
        "show me what products you have",
        "show me what you have",
        "i want to see products",
        "i want to see the products",
        "i want to see all the products",
        "show me the products",
        "show products",
        "show me products",
        "what do you have",
        "what all do you have",
        "available products",
        "show catalog",
        "show menu",
        "products",
        "catalog",
        "menu",
    }
    return (
        lowered in generic_phrases
        or "products do you have" in lowered
        or "what do you have" in lowered
        or ("products" in lowered and any(term in lowered for term in {"show", "see", "browse"}))
    )
