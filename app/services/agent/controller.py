from __future__ import annotations

from typing import Any, Dict

from app.services.agent.learning import (
    get_learning_profile,
    record_event,
    record_feedback_signal,
    update_learning_profile,
)
from app.services.agent.main_agent import run_agent
from app.services.agent.metrics import record_agent_run_metrics, record_metric
from app.services.agent.memory import (
    build_memory_bundle,
    get_state,
    note_intent,
    persist_state,
    save_turn,
    store_turn_memories,
    update_language,
)
from app.services.agent.observability import log_agent_trace
from app.services.agent.reflection import infer_feedback_signal
from app.services.agent.executor import handle_pending_action
from app.services.intelligence.customer_profile import get_customer_profile, update_last_active


async def process_incoming_message(message: str, user: Dict[str, Any]) -> str:
    business_id = user.get("business_id")
    state = get_state(user["phone"], business_id)

    pending_reply = handle_pending_action(message, user, state)
    if pending_reply:
        save_turn(user["phone"], message, pending_reply, business_id)
        return pending_reply

    style = update_language(user, message, business_id)
    profile = get_customer_profile(user["phone"], business_id)
    learning_profile = get_learning_profile(user["phone"], business_id)
    memory_payload = build_memory_bundle(user["phone"], business_id, state, style)

    update_last_active(user["phone"], business_id)
    record_event("incoming_message", business_id=business_id, phone=user["phone"], payload={"role": user.get("role"), "message": message})

    result = await run_agent(
        user_message=message,
        user=user,
        state=state,
        memory_payload=memory_payload,
        profile=profile,
        learning_profile=learning_profile,
        style=style,
    )

    updated_state = dict(result.updated_state)
    updated_state["lastLanguage"] = style
    updated_state["lastMessage"] = message
    feedback = infer_feedback_signal(message)
    updated_state["feedback"] = feedback
    note_intent(user["phone"], business_id, updated_state, result.intent, float(result.metrics.get("analysis_confidence") or 0.0))
    persist_state(user["phone"], business_id, updated_state, user["role"])

    save_turn(user["phone"], message, result.reply, business_id)
    store_turn_memories(
        user["phone"],
        business_id,
        user_message=message,
        bot_reply=result.reply,
        state=updated_state,
        intent=result.intent,
    )
    update_learning_profile(
        user["phone"],
        business_id,
        message=message,
        inferred_intent=result.intent,
        tool_name=result.tool_trace[-1].tool if result.tool_trace else "",
        tool_ok=result.tool_trace[-1].ok if result.tool_trace else None,
        products=result.tool_trace[-1].data.get("products", []) if result.tool_trace else [],
        vendor_status=result.tool_trace[-1].data.get("status", "") if result.tool_trace else "",
    )
    record_feedback_signal(user["phone"], business_id, sentiment=feedback["sentiment"], score=float(feedback["score"]))
    record_event(
        "agent_reply",
        business_id=business_id,
        phone=user["phone"],
        payload={
            "intent": result.intent,
            "reply": result.reply,
            "metrics": result.metrics,
            "tools": [trace.tool for trace in result.tool_trace],
        },
    )
    record_agent_run_metrics(
        business_id,
        phone=user["phone"],
        intent=result.intent,
        metrics=result.metrics,
        goal_tracking=updated_state.get("goal_tracking", {}),
    )
    if result.intent == "checkout" and updated_state.get("last_order_id"):
        record_metric(business_id, metric="order_completion", dimensions={"phone": user["phone"]})
    if result.intent == "support":
        record_metric(business_id, metric="support_handled", dimensions={"phone": user["phone"]})
    log_agent_trace(
        business_id=business_id,
        phone=user["phone"],
        role=str(user.get("role") or "customer"),
        message=message,
        analysis={
            "intent": result.intent,
            "confidence": result.metrics.get("analysis_confidence"),
            "uncertainty": result.metrics.get("uncertainty"),
        },
        strategy={
            "llm_tier": result.metrics.get("llm_tier"),
            "complexity": result.metrics.get("complexity"),
        },
        goal_tracking=updated_state.get("goal_tracking", {}),
        decisions=[
            {
                "action": decision.action,
                "arguments": decision.arguments,
                "rationale": decision.rationale,
                "confidence": decision.confidence,
                "plan_steps": decision.plan_steps,
            }
            for decision in result.decision_trace
        ],
        tools=[
            {
                "tool": trace.tool,
                "ok": trace.ok,
                "error": trace.error,
                "data": trace.data,
            }
            for trace in result.tool_trace
        ],
        reflections=result.reflections,
        metrics=result.metrics,
    )
    return result.reply
