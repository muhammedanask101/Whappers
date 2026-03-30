from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from app.services.agent.localization import contact_reply_prompt, style_text
from app.services.agent.planner import choose_next_action
from app.services.agent.policy import analyze_message, build_strategy, select_cost_tier
from app.services.agent.reflection import reflect_step, summarize_run
from app.services.agent.tools import execute_tool, is_useful_tool_observation
from app.services.agent.types import AgentRunResult, PlannerDecision, ToolObservation
from app.services.agent.validator import validate_action
from app.services.agent.memory import update_goal_tracking


def _log_main_agent(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[agent.main] {event} {normalized}".rstrip())


MAX_STEPS = 4
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
    return await asyncio.to_thread(
        _run_agent_sync,
        user_message,
        user,
        state,
        memory_payload,
        profile,
        learning_profile,
        style,
    )


def _run_agent_sync(
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
    _log_main_agent("run_start", phone=user.get("phone"), role=user.get("role"), intent=analysis.get("intent"), confidence=analysis.get("confidence"), uncertainty=analysis.get("uncertainty"), llm_tier=strategy.get("llm_tier"), workflow=(state.get("workflow") or {}).get("stage"))

    scratchpad: List[ToolObservation] = []
    decisions: List[PlannerDecision] = []
    reflections: List[Dict[str, Any]] = []
    working_state = dict(state)
    working_memory = dict(memory_payload)
    final_reply = ""
    working_state["tool_failures"] = dict(state.get("tool_failures") or {})
    working_state["llm_calls_this_run"] = 0
    working_state["planner_second_pass_used"] = False
    working_state["last_tool_used"] = str(state.get("last_tool_used") or "")
    working_state["same_tool_counter"] = int(state.get("same_tool_counter") or 0)
    working_state["force_deterministic_fallback"] = False
    working_state["executed_tool_signatures"] = list(state.get("executed_tool_signatures") or [])
    working_state["goal_tracking"] = update_goal_tracking(working_state, intent=analysis["intent"], user_message=user_message)
    seen_signatures = set(str(item) for item in working_state["executed_tool_signatures"])

    if _should_clarify_before_execution(analysis):
        final_reply = _clarification_message(analysis["intent"], style)
        working_state["uncertainty"] = {"score": analysis.get("uncertainty", 0.0), "reason": "high_uncertainty_pre_execution"}
        return AgentRunResult(
            reply=final_reply,
            intent=analysis["intent"],
            tool_trace=[],
            decision_trace=[],
            updated_state=working_state,
            metrics={
                "llm_tier": strategy.get("llm_tier"),
                "analysis_confidence": analysis.get("confidence"),
                "uncertainty": analysis.get("uncertainty"),
                "steps": 0,
                "complexity": analysis.get("complexity"),
                "goal_progress": (working_state.get("goal_tracking") or {}).get("progress_score", 0.0),
                "reflection_failures": 0,
                "planner_provider": "",
                "planner_model": "",
                "second_pass_used": False,
            },
            reflections=[],
        )

    for step in range(MAX_STEPS):
        _log_main_agent("step_start", step=step + 1, scratchpad_len=len(scratchpad), goal=(working_state.get("goal_tracking") or {}).get("goal_text"))
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
        _log_main_agent("decision", step=step + 1, action=decision.action, confidence=decision.confidence, rationale=decision.rationale, arguments=decision.arguments)
        working_state["goal_tracking"] = update_goal_tracking(
            working_state,
            intent=analysis["intent"],
            user_message=user_message,
            decision={"plan_steps": decision.plan_steps, "goal": decision.goal},
        )

        if decision.action == "respond":
            _log_main_agent("respond_decision", step=step + 1, message_preview=(decision.message or "")[:240])
            final_reply = decision.message or _fallback_message(analysis["intent"], strategy, style)
            break

        valid, error = validate_action(decision.action, decision.arguments)
        if not valid:
            _log_main_agent("validation_failed", step=step + 1, action=decision.action, error=error)
            scratchpad.append(ToolObservation(tool=decision.action, ok=False, message="", error=error))
            _record_tool_failure(working_state, decision.action)
            analysis["needs_llm"] = True
            analysis["uncertainty"] = min(1.0, analysis.get("uncertainty", 0.0) + 0.2)
            _relax_workflow_if_stuck(working_state, decision.action, analysis, user_message)
            continue

        decision_signature = f"{decision.action}:{repr(sorted(decision.arguments.items()))}"
        if decision_signature in seen_signatures:
            final_reply = _converged_fallback_message(decision.action, analysis["intent"], style)
            working_state["force_deterministic_fallback"] = True
            break
        seen_signatures.add(decision_signature)
        working_state["executed_tool_signatures"] = list(seen_signatures)[-12:]

        _log_main_agent("tool_execute_start", step=step + 1, action=decision.action)
        before_state = _progress_snapshot(working_state)
        observation = execute_tool(
            decision.action,
            decision.arguments | {"profile": profile, "learning": learning_profile, "memory": working_memory},
            working_state,
            user,
            style,
            working_memory,
        )
        scratchpad.append(observation)
        _log_main_agent("tool_execute_complete", step=step + 1, tool=observation.tool, ok=observation.ok, error=observation.error, message_preview=observation.message[:240])
        _merge_state_patch(working_state, observation.state_patch)
        _merge_memory_from_observation(working_memory, observation)
        made_progress = _made_progress(decision.action, before_state, working_state, observation)
        _update_tool_loop_state(working_state, decision.action, made_progress)
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
            _log_main_agent("tool_failed", step=step + 1, tool=observation.tool, uncertainty_before=analysis.get("uncertainty"))
            _record_tool_failure(working_state, observation.tool)
            analysis["needs_llm"] = True
            analysis["uncertainty"] = min(1.0, analysis.get("uncertainty", 0.0) + 0.22)
            _relax_workflow_if_stuck(working_state, observation.tool, analysis, user_message)
            if int((working_state.get("tool_failures") or {}).get(observation.tool, 0)) >= 2:
                final_reply = _converged_fallback_message(observation.tool, analysis["intent"], style)
                working_state["force_deterministic_fallback"] = True
                break
            if step == MAX_STEPS - 1:
                final_reply = observation.error or _fallback_message(analysis["intent"], strategy, style)
            continue

        _clear_tool_failures(working_state, observation.tool)
        if not made_progress and _is_state_mutating_tool(decision.action):
            _record_tool_failure(working_state, observation.tool)
            working_state["last_tool_gap"] = {"tool": observation.tool, "reason": "no_progress", "query": decision.arguments.get("query") or decision.arguments.get("raw_message") or user_message}
            working_state["force_deterministic_fallback"] = True
            final_reply = _converged_fallback_message(observation.tool, analysis["intent"], style)
            break
        useful = is_useful_tool_observation(observation)
        if not useful:
            _log_main_agent("tool_not_useful", step=step + 1, tool=observation.tool, message_preview=observation.message[:160])
            _record_tool_failure(working_state, observation.tool)
            analysis["needs_llm"] = True
            analysis["uncertainty"] = min(1.0, analysis.get("uncertainty", 0.0) + 0.18)
            working_state["last_tool_gap"] = {
                "tool": observation.tool,
                "reason": "not_useful",
                "query": decision.arguments.get("query") or decision.arguments.get("raw_message") or user_message,
            }
            _relax_workflow_if_stuck(working_state, observation.tool, analysis, user_message)
            continue

        if int(working_state.get("same_tool_counter", 0)) >= 2:
            working_state["force_deterministic_fallback"] = True
            final_reply = _converged_fallback_message(observation.tool, analysis["intent"], style)
            break

        if _should_replan_after_observation(decision, observation, analysis) and step < MAX_STEPS - 1:
            _log_main_agent("tool_replan", step=step + 1, tool=observation.tool, intent=analysis.get("intent"))
            analysis["needs_llm"] = True
            analysis["uncertainty"] = min(1.0, max(analysis.get("uncertainty", 0.0), 0.48))
            working_state["last_tool_gap"] = {
                "tool": observation.tool,
                "reason": "weak_or_empty_result",
                "query": decision.arguments.get("query") or decision.arguments.get("raw_message") or user_message,
            }
            continue

        if decision.action in INTERNAL_TOOLS:
            continue

        working_state["last_tool_gap"] = {}
        final_reply = observation.message
        break

    if not final_reply:
        last_error = next((row.error for row in reversed(scratchpad) if row.error), "")
        final_reply = last_error or _fallback_message(analysis["intent"], strategy, style)
    if analysis.get("uncertainty", 0.0) >= 0.72 and not any(is_useful_tool_observation(row) and not row.internal for row in scratchpad):
        final_reply = _clarification_message(analysis["intent"], style)

    working_state["uncertainty"] = {
        "score": analysis.get("uncertainty", 0.0),
        "reason": decisions[-1].rationale if decisions else "",
    }
    run_summary = summarize_run(reflections=reflections, final_reply=final_reply, state=working_state)
    if run_summary.get("recovery_needed") and not final_reply:
        final_reply = contact_reply_prompt(style)
    _log_main_agent("run_complete", phone=user.get("phone"), final_reply_preview=final_reply[:240], steps=len(decisions), failures=run_summary.get("failures"), uncertainty=analysis.get("uncertainty"))

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
            "planner_provider": str(decisions[-1].metadata.get("provider") if decisions else ""),
            "planner_model": str(decisions[-1].metadata.get("model") if decisions else ""),
            "second_pass_used": bool(working_state.get("planner_second_pass_used")),
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


def _progress_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cart": dict(state.get("cart") or {}),
        "workflow": dict(state.get("workflow") or {}),
        "last_order_id": state.get("last_order_id", ""),
        "last_vendor_order_id": state.get("last_vendor_order_id", ""),
        "open_ticket_id": state.get("open_ticket_id", ""),
    }


def _made_progress(action: str, before: Dict[str, Any], after_state: Dict[str, Any], observation: ToolObservation) -> bool:
    if not _is_state_mutating_tool(action):
        return True
    if action == "manage_cart" and observation.data.get("items"):
        return True
    if action == "collect_address" and observation.data.get("address"):
        return True
    if action == "checkout_order" and observation.data.get("order_id"):
        return True
    after = _progress_snapshot(after_state)
    return before != after


def _is_state_mutating_tool(action: str) -> bool:
    return action in {"manage_cart", "collect_address", "checkout_order", "create_support_ticket", "resolve_support_ticket", "vendor_update_order_status", "update_order_status", "bulk_update_orders"}


def _update_tool_loop_state(state: Dict[str, Any], tool: str, made_progress: bool) -> None:
    if state.get("last_tool_used") == tool and not made_progress:
        state["same_tool_counter"] = int(state.get("same_tool_counter", 0)) + 1
    else:
        state["same_tool_counter"] = 0 if made_progress else 1
    state["last_tool_used"] = tool


def _should_clarify_before_execution(analysis: Dict[str, Any]) -> bool:
    if float(analysis.get("uncertainty", 0.0)) <= 0.4:
        return False
    if analysis.get("cart_view_request"):
        return False
    if analysis.get("structured_cart_payload"):
        return False
    if analysis.get("probable_address"):
        return False
    return analysis.get("intent") in {"cart", "checkout", "order_status", "support"}


def _record_tool_failure(state: Dict[str, Any], tool: str) -> None:
    if not tool:
        return
    failures = dict(state.get("tool_failures") or {})
    failures[tool] = int(failures.get(tool, 0)) + 1
    state["tool_failures"] = failures


def _clear_tool_failures(state: Dict[str, Any], tool: str) -> None:
    if not tool:
        return
    failures = dict(state.get("tool_failures") or {})
    failures.pop(tool, None)
    state["tool_failures"] = failures


def _relax_workflow_if_stuck(state: Dict[str, Any], tool: str, analysis: Dict[str, Any], user_message: str) -> None:
    workflow = dict(state.get("workflow") or {})
    if workflow.get("name") != "order_checkout":
        return
    if tool == "collect_address" and (analysis.get("intent") == "cart" or analysis.get("cart_extraction_required")):
        workflow["stage"] = ""
        workflow["next_question"] = ""
        state["workflow"] = workflow
        analysis["order_workflow_locked"] = False
        return
    failures = int((state.get("tool_failures") or {}).get(tool, 0))
    if failures < 2:
        return
    # Repeated failure on the same step should loosen workflow bias so the planner can recover.
    workflow["stage"] = ""
    workflow["next_question"] = ""
    state["workflow"] = workflow
    state["last_tool_gap"] = {"tool": tool, "reason": "repeated_failure", "query": user_message}
    analysis["order_workflow_locked"] = False


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


def _converged_fallback_message(tool: str, intent: str, style: str) -> str:
    if tool == "manage_cart":
        return style_text(style, "I need the exact product name, quantity, and operation like add, remove, or set.", "Exact product name, quantity, add/remove/set ennu parayu.", "Exact product name, quantity, add/remove/set ennu parayu.")
    if tool == "collect_address":
        return style_text(style, "Please send the full address as: house or building, area, landmark if any, and 6-digit PIN.", "Full address house/building, area, landmark undenkil athum, 6-digit PIN format-il ayakku.", "Full address house/building, area, landmark undenkil athum, 6-digit PIN format-il ayakku.")
    return _clarification_message(intent, style)


def _should_replan_after_observation(decision: PlannerDecision, observation: ToolObservation, analysis: Dict[str, Any]) -> bool:
    if not is_useful_tool_observation(observation):
        return True
    if decision.action == "search_catalog":
        if observation.data.get("recovery_needed"):
            return True
        if analysis.get("intent") == "browse" and not observation.data.get("products"):
            return True
    if decision.action == "recommend_products" and analysis.get("intent") == "browse" and not observation.data.get("products"):
        return True
    return False
