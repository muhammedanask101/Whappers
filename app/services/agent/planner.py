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

    llm_budget = _llm_call_budget(state, analysis)
    if state.get("force_deterministic_fallback") or int(state.get("llm_calls_this_run", 0)) >= llm_budget:
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
        metadata={"provider": payload.get("provider"), "model": payload.get("model")},
    )
    if _needs_second_pass(decision, analysis, state, scratchpad) and int(state.get("llm_calls_this_run", 0)) < llm_budget:
        state["llm_calls_this_run"] = int(state.get("llm_calls_this_run", 0)) + 1
        state["planner_second_pass_used"] = True
        stronger_payload = call_agent_llm(messages, tier="strong", second_pass=True)
        decision = PlannerDecision(
            action=str(stronger_payload.get("action") or "respond").strip() or "respond",
            arguments=dict(stronger_payload.get("arguments") or {}),
            message=str(stronger_payload.get("message") or "").strip(),
            rationale=str(stronger_payload.get("thought") or "").strip() or "second_pass_recovery",
            confidence=float(stronger_payload.get("confidence") or decision.confidence),
            plan_steps=list(stronger_payload.get("plan_steps") or decision.plan_steps),
            goal=dict(stronger_payload.get("goal") or decision.goal),
            uncertainty=float(stronger_payload.get("uncertainty") or decision.uncertainty),
            metadata={"provider": stronger_payload.get("provider"), "model": stronger_payload.get("model")},
        )
    decision = _enforce_workflow_lock(decision, user_message, state, analysis)
    _log_planner("llm_planner_result", action=decision.action, confidence=decision.confidence, rationale=decision.rationale, arguments=decision.arguments)
    return decision


def _micro_decision(user_message: str, user: Dict[str, Any], state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision:
    lowered = user_message.strip().lower()
    style = state.get("lastLanguage", "english")
    if analysis.get("cart_view_request") and (state.get("cart") or {}).get("items"):
        return PlannerDecision(action="manage_cart", arguments={"operation": "view", "raw_message": user_message}, rationale="cart_view_shortcut", confidence=0.88)
    if analysis.get("intent") == "general":
        return PlannerDecision(
            action="respond",
            message=style_text(style, "Hi. Tell me what you want to do: browse products, place an order, track an order, or get help.", "Hi. Ningalkku browse cheyyano, order cheyyano, order track cheyyano, allenkil help veeno ennu parayu.", "Hi. Ningalkku browse cheyyano, order cheyyano, order track cheyyano, allenkil help veeno ennu parayu."),
            rationale="cheap_general_shortcut",
            confidence=0.58,
        )
    if (state.get("workflow") or {}).get("stage") == "ready_to_checkout" and lowered in {"yes", "ok", "okay", "confirm", "place", "proceed"}:
        return PlannerDecision(action="checkout_order", arguments={"raw_message": user_message}, rationale="checkout_confirmation_shortcut", confidence=0.86)
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
    analysis = context.get("analysis") or {}
    if any(term in lowered for term in {"list orders", "show orders", "recent orders"}):
        return "list_orders"
    if any(term in lowered for term in {"sales summary", "sales today", "summarize sales"}):
        return "summarize_sales"
    if any(term in lowered for term in {"top products", "best sellers"}):
        return "get_top_products"
    if any(term in lowered for term in {"business health", "performance metrics"}):
        return "get_business_health_metrics"
    if any(term in lowered for term in {"pending orders", "delivered orders", "accepted orders", "rejected orders"}):
        return "filter_orders"
    if any(term in lowered for term in {"analytics", "summary", "business summary"}):
        return "summarize_business"
    if intent == "browse":
        return "search_catalog"
    if intent == "order_status":
        return "get_order_status"
    if intent == "checkout" and lowered in {"confirm", "yes", "place order"} and workflow.get("stage") == "ready_to_checkout":
        return "checkout_order"
    if intent == "cart":
        return "manage_cart"
    if workflow.get("stage") == "collect_address" and analysis.get("probable_address"):
        return "collect_address"
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
    if action == "list_orders":
        filter_by = ""
        for candidate in ["today", "pending", "delivered"]:
            if candidate in lowered:
                filter_by = candidate
                break
        arguments: Dict[str, Any] = {"raw_message": message}
        if filter_by:
            arguments["filter"] = filter_by
        return PlannerDecision(action="list_orders", arguments=arguments, rationale="intent_safety_route", confidence=0.82)
    if action == "filter_orders":
        status = ""
        for candidate in ["pending", "accepted", "rejected", "delivered"]:
            if candidate in lowered:
                status = candidate
                break
        return PlannerDecision(action="filter_orders", arguments={"status": status, "raw_message": message}, rationale="intent_safety_route", confidence=0.82)
    if action == "summarize_sales":
        return PlannerDecision(action="summarize_sales", arguments={"time_window": "7d", "raw_message": message}, rationale="intent_safety_route", confidence=0.84)
    if action == "get_top_products":
        return PlannerDecision(action="get_top_products", arguments={"time_window": "30d", "raw_message": message}, rationale="intent_safety_route", confidence=0.84)
    if action == "get_business_health_metrics":
        return PlannerDecision(action="get_business_health_metrics", arguments={"raw_message": message}, rationale="intent_safety_route", confidence=0.84)
    if action == "summarize_business":
        return PlannerDecision(action="summarize_business", arguments={"raw_message": message}, rationale="intent_safety_route", confidence=0.8)
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


def _llm_call_budget(state: Dict[str, Any], analysis: Dict[str, Any]) -> int:
    if float(analysis.get("uncertainty") or 0.0) >= 0.55:
        return 2
    if state.get("last_tool_gap") or (state.get("tool_failures") or {}):
        return 2
    return 1


def _needs_second_pass(
    decision: PlannerDecision,
    analysis: Dict[str, Any],
    state: Dict[str, Any],
    scratchpad: List[ToolObservation],
) -> bool:
    if not analysis.get("needs_llm"):
        return False
    if state.get("planner_second_pass_used"):
        return False
    if decision.action == "respond" and float(decision.confidence or 0.0) < 0.45:
        return True
    if float(decision.uncertainty or 0.0) >= 0.7:
        return True
    if decision.action and int((state.get("tool_failures") or {}).get(decision.action, 0)) > 0:
        return True
    if scratchpad and not scratchpad[-1].ok:
        return True
    return False
