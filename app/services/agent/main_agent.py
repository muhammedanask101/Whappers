from __future__ import annotations

from typing import Any, Dict, List

from app.services.agent.localization import contact_reply_prompt, style_text
from app.services.agent.planner import choose_next_action
from app.services.agent.policy import analyze_message, build_strategy, select_cost_tier
from app.services.agent.reflection import reflect_step, summarize_run
from app.services.agent.tools import execute_tool
from app.services.agent.types import AgentRunResult, PlannerDecision, ToolObservation
from app.services.agent.validator import validate_action
from app.services.agent.memory import update_goal_tracking


MAX_STEPS = 5
INTERNAL_TOOLS = {
    "inspect_customer_context",
    "inspect_order_context",
    "inspect_support_context",
    "inspect_business_context",
    "inspect_memory_context",
}


async def run_agent(
    user_message: str,
    user: Dict[str, Any],
    state: Dict[str, Any],
    memory_payload: Dict[str, Any],
    profile: Dict[str, Any],
    learning_profile: Dict[str, Any],
    style: str,
) -> AgentRunResult:
    analysis = analyze_message(user_message, user=user, state=state, learning_profile=learning_profile)
    strategy = build_strategy(user=user, profile=profile, analysis=analysis, state=state)
    strategy["llm_tier"] = select_cost_tier(analysis, state, user)

    scratchpad: List[ToolObservation] = []
    decisions: List[PlannerDecision] = []
    reflections: List[Dict[str, Any]] = []
    working_state = dict(state)
    working_memory = dict(memory_payload)
    final_reply = ""
    working_state["goal_tracking"] = update_goal_tracking(working_state, intent=analysis["intent"], user_message=user_message)

    for step in range(MAX_STEPS):
        decision = choose_next_action(
            user_message=user_message,
            user=user,
            state=working_state,
            memory_payload=working_memory,
            analysis=analysis,
            strategy=strategy,
            learning_profile=learning_profile,
            scratchpad=scratchpad,
            max_steps=MAX_STEPS,
        )
        decisions.append(decision)
        working_state["goal_tracking"] = update_goal_tracking(
            working_state,
            intent=analysis["intent"],
            user_message=user_message,
            decision={"plan_steps": decision.plan_steps, "goal": decision.goal},
        )

        if decision.action == "respond":
            final_reply = decision.message or _fallback_message(analysis["intent"], strategy, style)
            break

        valid, error = validate_action(decision.action, decision.arguments)
        if not valid:
            scratchpad.append(ToolObservation(tool=decision.action, ok=False, message="", error=error))
            analysis["needs_llm"] = True
            analysis["uncertainty"] = min(1.0, analysis.get("uncertainty", 0.0) + 0.2)
            continue

        observation = execute_tool(
            decision.action,
            decision.arguments | {"profile": profile, "learning": learning_profile, "memory": working_memory},
            working_state,
            user,
            style,
            working_memory,
        )
        scratchpad.append(observation)
        _merge_state_patch(working_state, observation.state_patch)
        _merge_memory_from_observation(working_memory, observation)
        working_state["goal_tracking"] = update_goal_tracking(
            working_state,
            intent=analysis["intent"],
            user_message=user_message,
            observation={"tool": observation.tool, "ok": observation.ok},
        )
        reflection = reflect_step(step=step + 1, decision=decision, observation=observation, state=working_state)
        reflections.append(reflection)
        working_state["last_reflection"] = reflection

        if observation.events:
            for event in observation.events:
                pass

        if not observation.ok:
            analysis["needs_llm"] = True
            analysis["uncertainty"] = min(1.0, analysis.get("uncertainty", 0.0) + 0.22)
            if step == MAX_STEPS - 1:
                final_reply = observation.error or _fallback_message(analysis["intent"], strategy, style)
            continue

        if decision.action in INTERNAL_TOOLS:
            continue

        final_reply = observation.message

        if decision.action in {"collect_address"} and working_state.get("workflow", {}).get("stage") == "ready_to_checkout":
            analysis["needs_llm"] = False
            continue

        if decision.action in {"manage_cart", "search_catalog", "get_product_details", "recommend_products", "get_order_status", "create_support_ticket", "checkout_order", "vendor_update_order_status", "resolve_support_ticket"}:
            break

    if not final_reply:
        last_error = next((row.error for row in reversed(scratchpad) if row.error), "")
        final_reply = last_error or _fallback_message(analysis["intent"], strategy, style)
    if analysis.get("uncertainty", 0.0) >= 0.72 and not any(row.ok and not row.internal for row in scratchpad):
        final_reply = _clarification_message(analysis["intent"], style)

    working_state["uncertainty"] = {
        "score": analysis.get("uncertainty", 0.0),
        "reason": decisions[-1].rationale if decisions else "",
    }
    run_summary = summarize_run(reflections=reflections, final_reply=final_reply, state=working_state)
    if run_summary.get("recovery_needed") and not final_reply:
        final_reply = contact_reply_prompt(style)

    return AgentRunResult(
        reply=final_reply,
        intent=analysis["intent"],
        tool_trace=scratchpad,
        decision_trace=decisions,
        updated_state=working_state,
        metrics={
            "llm_tier": strategy.get("llm_tier"),
            "analysis_confidence": analysis.get("confidence"),
            "uncertainty": analysis.get("uncertainty"),
            "steps": len(decisions),
            "complexity": analysis.get("complexity"),
            "goal_progress": (working_state.get("goal_tracking") or {}).get("progress_score", 0.0),
            "reflection_failures": run_summary.get("failures", 0),
        },
        reflections=reflections,
    )


def _merge_state_patch(state: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            merged = dict(state[key])
            merged.update(value)
            state[key] = merged
        else:
            state[key] = value


def _merge_memory_from_observation(memory: Dict[str, Any], observation: ToolObservation) -> None:
    if observation.tool == "inspect_customer_context":
        memory["customer_context"] = observation.data
    elif observation.tool == "inspect_order_context":
        memory["order_context"] = observation.data
    elif observation.tool == "inspect_support_context":
        memory["support_context"] = observation.data
    elif observation.tool == "inspect_business_context":
        memory["business_context"] = observation.data
    elif observation.tool == "inspect_memory_context":
        memory["semantic_context"] = observation.data.get("memories", [])


def _fallback_message(intent: str, strategy: Dict[str, Any], style: str) -> str:
    if strategy.get("support_guard"):
        return style_text(style, "Please describe the issue in one message and I will work through it.", "Issue oru message-il parayu, njan ath handle cheyyam.", "പ്രശ്നം ഒരു മെസേജിൽ പറയൂ, ഞാൻ അത് കൈകാര്യം ചെയ്യും.")
    if intent == "cart":
        return style_text(style, "Tell me the product and quantity you want, and I will build the order.", "Venda productum quantityum parayu, njan order ready aakkam.", "വേണ്ട ഉൽപ്പന്നവും അളവും പറയൂ, ഞാൻ ഓർഡർ തയ്യാറാക്കാം.")
    if intent == "checkout":
        return style_text(style, "Share your delivery address and I will prepare the order.", "Delivery address ayachu tharuo, njan order prepare cheyyam.", "ഡെലിവറി വിലാസം അയച്ചു തരൂ, ഞാൻ ഓർഡർ തയ്യാറാക്കാം.")
    return contact_reply_prompt(style)


def _clarification_message(intent: str, style: str) -> str:
    if intent == "cart":
        return style_text(style, "Please tell me the exact product name and quantity you want.", "Exact product nameum quantityum parayu.", "കൃത്യമായ ഉൽപ്പന്ന നാമവും അളവും പറയൂ.")
    if intent == "order_status":
        return style_text(style, "Please share the order ID or the phone number used for the order.", "Order ID allenkil orderinu upayogicha number parayu.", "ഓർഡർ ഐഡിയോ ഓർഡറിന് ഉപയോഗിച്ച ഫോൺ നമ്പറോ പറയൂ.")
    if intent == "support":
        return style_text(style, "Please describe the issue, what went wrong, and any related order ID.", "Issue enthaanu, enthaanu wrong ayathu, order ID undenkil athum parayu.", "പ്രശ്നം എന്താണ്, എന്താണ് തെറ്റായത്, ബന്ധപ്പെട്ട ഓർഡർ ഐഡി ഉണ്ടെങ്കിൽ അതും പറയൂ.")
    return contact_reply_prompt(style)
