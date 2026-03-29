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
    persisted = _planned_decision_from_state(user_message, state, scratchpad, analysis)
    if persisted:
        _log_planner("persisted_plan_used", action=persisted.action, confidence=persisted.confidence, rationale=persisted.rationale)
        return persisted

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

    messages = build_planner_messages(
        user_message=user_message,
        user=user,
        memory=memory_payload,
        tools=tool_ranking[:10],
        scratchpad=[_observation_to_prompt(row) for row in scratchpad[-max_steps:]],
        strategy=strategy | {"tool_shortlist": [item["name"] for item in tool_ranking[:6]]},
        analysis=analysis,
    )
    try:
        _log_planner("llm_planner_start", tier=strategy.get("llm_tier", "balanced"), top_tools=[item["name"] for item in tool_ranking[:5]], uncertainty=analysis.get("uncertainty"))
        payload = call_agent_llm(messages, tier=strategy.get("llm_tier", "balanced"))
    except Exception:
        _log_planner("llm_planner_failed", tier=strategy.get("llm_tier", "balanced"))
        return _ranked_fallback(user_message, tool_ranking, state, analysis)

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return _ranked_fallback(user_message, tool_ranking, state, analysis)

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
    safe_response = _safe_fallback_response(user_message, state, analysis)
    if safe_response:
        return safe_response
    for tool in tool_ranking:
        name = tool["name"]
        if analysis.get("order_workflow_locked") and name not in {"manage_cart", "collect_address", "checkout_order", "inspect_order_context", "repair_order_context"}:
            continue
        if name == "repair_order_context" and analysis.get("is_correction"):
            return PlannerDecision(action=name, arguments={"raw_message": user_message}, rationale="ranked_correction_fallback", confidence=0.42)
        if name == "manage_cart" and (analysis.get("intent") == "cart" or analysis.get("cart_extraction_required") or analysis.get("cart_view_request")):
            arguments = {"raw_message": user_message}
            if analysis.get("cart_view_request"):
                arguments["operation"] = "view"
            else:
                arguments["query"] = user_message
            return PlannerDecision(action=name, arguments=arguments, rationale="ranked_cart_fallback", confidence=0.46)
        if name == "collect_address" and analysis.get("probable_address"):
            return PlannerDecision(action=name, arguments={"address": user_message, "raw_message": user_message}, rationale="ranked_address_fallback", confidence=0.44)
        if name == "checkout_order" and analysis.get("intent") == "checkout":
            return PlannerDecision(action=name, arguments={"raw_message": user_message}, rationale="ranked_checkout_fallback", confidence=0.4)
        if name == "inspect_order_context":
            return PlannerDecision(action=name, arguments={"raw_message": user_message}, rationale="ranked_fallback_context", confidence=0.35)
        if analysis.get("confidence", 0.0) >= 0.55 and name in {"search_catalog", "recommend_products", "get_order_status"}:
            return PlannerDecision(action=name, arguments={"query": user_message, "raw_message": user_message}, rationale="ranked_fallback", confidence=0.28)
    return PlannerDecision(
        action="respond",
        message=style_text(state.get("lastLanguage", "english"), "Please tell me a bit more so I can help properly.", "Kurachu koodi clear ayi parayuo, njan nannayi sahayikkam.", "Kurachu koodi clear ayi parayuo, njan nannayi sahayikkam."),
        rationale="ranked_fallback_respond",
        confidence=0.2,
    )


def _safe_fallback_response(user_message: str, state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision | None:
    lowered = user_message.strip().lower()
    style = state.get("lastLanguage", "english")
    if analysis.get("order_workflow_locked"):
        if analysis.get("cart_view_request") and (state.get("cart") or {}).get("items"):
            return PlannerDecision(action="manage_cart", arguments={"operation": "view", "raw_message": user_message}, rationale="locked_cart_view", confidence=0.64)
        if analysis.get("probable_address"):
            return PlannerDecision(action="collect_address", arguments={"address": user_message, "raw_message": user_message}, rationale="locked_address_progress", confidence=0.66)
        if analysis.get("intent") in {"cart", "checkout"} or analysis.get("cart_extraction_required"):
            return PlannerDecision(action="manage_cart", arguments={"query": user_message, "raw_message": user_message}, rationale="locked_cart_progress", confidence=0.62)
    if analysis.get("intent") == "general" or lowered in {"hi", "hello", "hey", "hey there", "hey theree", "hii"}:
        return PlannerDecision(
            action="respond",
            message=style_text(style, "Hi. Tell me what you want to do: place an order, check an order, or get help.", "Hi. Ningalkku entha venam ennu parayu: order cheyyano, order check cheyyano, allenkil help veeno.", "Hi. Ningalkku entha venam ennu parayu: order cheyyano, order check cheyyano, allenkil help veeno."),
            rationale="safe_greeting_fallback",
            confidence=0.6,
        )
    if analysis.get("intent") == "cart":
        return PlannerDecision(action="manage_cart", arguments={"query": user_message, "raw_message": user_message}, rationale="safe_cart_execution", confidence=0.58)
    if analysis.get("intent") == "browse" and _looks_like_catalog_browse(lowered) and not (state.get("cart") or {}).get("items"):
        return PlannerDecision(action="search_catalog", arguments={"query": user_message, "raw_message": user_message}, rationale="safe_browse_catalog_fallback", confidence=0.54)
    return None


def _planned_decision_from_state(user_message: str, state: Dict[str, Any], scratchpad: List[ToolObservation], analysis: Dict[str, Any]) -> PlannerDecision | None:
    workflow = state.get("workflow") or {}
    if analysis.get("order_workflow_locked"):
        if analysis.get("cart_view_request") and (state.get("cart") or {}).get("items"):
            return PlannerDecision(action="manage_cart", arguments={"operation": "view"}, rationale="locked_workflow_cart_view", confidence=0.84)
        if workflow.get("stage") == "collect_address" and analysis.get("probable_address"):
            return PlannerDecision(action="collect_address", arguments={}, rationale="locked_workflow_collect_address", confidence=0.84)
        if analysis.get("intent") in {"cart", "checkout"} or analysis.get("cart_extraction_required"):
            return PlannerDecision(action="manage_cart", arguments={"query": user_message, "raw_message": user_message}, rationale="locked_workflow_manage_cart", confidence=0.78)

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
    if analysis.get("is_correction") and decision.action in {"collect_address", "checkout_order"}:
        return True
    if decision.action == "collect_address" and not analysis.get("probable_address"):
        return True
    if analysis.get("cart_view_request") and decision.action != "manage_cart":
        return True
    return False


def _enforce_workflow_lock(decision: PlannerDecision, user_message: str, state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision:
    if not analysis.get("order_workflow_locked"):
        return decision
    allowed = {"manage_cart", "inspect_order_context", "collect_address", "checkout_order", "repair_order_context", "respond"}
    if decision.action not in allowed:
        return _safe_fallback_response(user_message, state, analysis) or PlannerDecision(action="manage_cart", arguments={"query": user_message, "raw_message": user_message}, rationale="workflow_lock_override", confidence=0.52)
    if analysis.get("cart_view_request") and decision.action != "manage_cart":
        return PlannerDecision(action="manage_cart", arguments={"operation": "view", "raw_message": user_message}, rationale="workflow_lock_cart_view", confidence=0.82)
    return decision


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
