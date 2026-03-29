from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services.agent.localization import style_text
from app.services.agent.policy import cheap_action_allowed
from app.services.agent.prompts import build_planner_messages
from app.services.agent.tools import rank_tools
from app.services.agent.types import PlannerDecision, ToolObservation
from app.services.llm import call_agent_llm


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
    persisted = _planned_decision_from_state(state, scratchpad)
    if persisted:
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
        payload = call_agent_llm(messages, tier=strategy.get("llm_tier", "balanced"))
    except Exception:
        return _ranked_fallback(user_message, tool_ranking, state)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return PlannerDecision(action="respond", message=payload.strip() or style_text(memory_payload.get("style", "english"), "Please tell me a bit more so I can help properly.", "Kurachu koodi clear ayi parayuo, njan nannayi handle cheyyam.", "കുറച്ച് കൂടി വ്യക്തമായി പറഞ്ഞാൽ ഞാൻ നന്നായി കൈകാര്യം ചെയ്യാം."), rationale="llm_non_json_fallback", confidence=0.25)
    return PlannerDecision(
        action=str(payload.get("action") or "respond").strip() or "respond",
        arguments=dict(payload.get("arguments") or {}),
        message=str(payload.get("message") or "").strip(),
        rationale=str(payload.get("thought") or "").strip(),
        confidence=float(payload.get("confidence") or 0.45),
        plan_steps=list(payload.get("plan_steps") or []),
        goal=dict(payload.get("goal") or {}),
        uncertainty=float(payload.get("uncertainty") or analysis.get("uncertainty") or 0.0),
    )


def _micro_decision(user_message: str, user: Dict[str, Any], state: Dict[str, Any], analysis: Dict[str, Any]) -> PlannerDecision:
    lowered = user_message.strip().lower()
    if user.get("role") == "vendor" and analysis.get("vendor_status_options"):
        return PlannerDecision(action="vendor_update_order_status", arguments={"status": analysis["vendor_status_options"][0]["status"], "raw_message": user_message}, rationale="strong_vendor_status_signal", confidence=0.88)
    if state.get("workflow", {}).get("stage") == "collect_address":
        return PlannerDecision(action="collect_address", arguments={"address": user_message, "raw_message": user_message}, rationale="address_capture_shortcut", confidence=0.86)
    if state.get("workflow", {}).get("stage") == "ready_to_checkout" and lowered in {"yes", "ok", "okay", "confirm", "place", "proceed"}:
        return PlannerDecision(action="checkout_order", arguments={"raw_message": user_message}, rationale="checkout_confirmation_shortcut", confidence=0.9)
    return PlannerDecision(action="respond", message=style_text(state.get("lastLanguage", "english"), "Tell me a little more and I will handle it.", "Kurachu koodi parayu, njan handle cheyyam.", "കുറച്ച് കൂടി പറയൂ, ഞാൻ കൈകാര്യം ചെയ്യാം."), rationale="micro_fallback", confidence=0.2)


def _ranked_fallback(user_message: str, tool_ranking: List[Dict[str, Any]], state: Dict[str, Any]) -> PlannerDecision:
    for tool in tool_ranking:
        name = tool["name"]
        if name == "inspect_order_context":
            return PlannerDecision(action=name, arguments={"raw_message": user_message}, rationale="ranked_fallback_context", confidence=0.35)
        if name in {"search_catalog", "recommend_products", "get_order_status"}:
            return PlannerDecision(action=name, arguments={"query": user_message, "raw_message": user_message}, rationale="ranked_fallback", confidence=0.28)
    return PlannerDecision(action="respond", message=style_text(state.get("lastLanguage", "english"), "Please tell me a bit more so I can help properly.", "Kurachu koodi clear ayi parayuo, njan nannayi sahayikkam.", "കുറച്ച് കൂടി വ്യക്തമായി പറഞ്ഞാൽ ഞാൻ നന്നായി സഹായിക്കാം."), rationale="ranked_fallback_respond", confidence=0.2)


def _observation_to_prompt(observation: ToolObservation) -> Dict[str, Any]:
    return {
        "tool": observation.tool,
        "ok": observation.ok,
        "message": observation.message,
        "error": observation.error,
        "data": observation.data,
        "internal": observation.internal,
    }


def _planned_decision_from_state(state: Dict[str, Any], scratchpad: List[ToolObservation]) -> PlannerDecision | None:
    goal_tracking = state.get("goal_tracking") or {}
    plan_steps = list(goal_tracking.get("current_plan") or [])
    if not plan_steps:
        return None
    recent_failures = {row.tool for row in scratchpad[-3:] if not row.ok}
    for step in plan_steps:
        action = str(step.get("action") or "").strip()
        if not action or action in recent_failures:
            continue
        return PlannerDecision(
            action=action,
            arguments=dict(step.get("arguments") or {}),
            rationale=str(step.get("reason") or "persisted_plan_step"),
            confidence=0.74,
            plan_steps=plan_steps,
            goal=dict(goal_tracking),
            uncertainty=0.18,
        )
    return None
