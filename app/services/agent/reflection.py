from __future__ import annotations

from typing import Any, Dict, List

from app.services.agent.types import PlannerDecision, ToolObservation


NEGATIVE_TOKENS = {"bad", "wrong", "issue", "problem", "late", "refund", "damaged", "cancel", "angry", "poor"}
POSITIVE_TOKENS = {"thanks", "thank", "good", "great", "perfect", "nice", "super", "resolved", "helpful"}


def reflect_step(
    *,
    step: int,
    decision: PlannerDecision,
    observation: ToolObservation,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    confidence = float(decision.confidence or 0.0)
    uncertainty = max(0.0, 1.0 - confidence)
    if not observation.ok:
        uncertainty = min(1.0, uncertainty + 0.25)
    if observation.internal and observation.ok:
        outcome = "context_gathered"
    elif observation.ok:
        outcome = "progressed"
    else:
        outcome = "failed"
    next_hint = _next_hint(observation, state)
    return {
        "step": step,
        "action": decision.action,
        "outcome": outcome,
        "ok": observation.ok,
        "confidence": round(confidence, 4),
        "uncertainty": round(uncertainty, 4),
        "next_hint": next_hint,
        "error": observation.error,
    }


def summarize_run(
    *,
    reflections: List[Dict[str, Any]],
    final_reply: str,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    failures = [row for row in reflections if not row.get("ok")]
    average_uncertainty = (
        sum(float(row.get("uncertainty", 0.0)) for row in reflections) / len(reflections)
        if reflections
        else 0.0
    )
    return {
        "steps": len(reflections),
        "failures": len(failures),
        "average_uncertainty": round(average_uncertainty, 4),
        "workflow_stage": state.get("workflow", {}).get("stage"),
        "has_reply": bool(final_reply.strip()),
        "recovery_needed": bool(failures and average_uncertainty >= 0.45),
    }


def infer_feedback_signal(message: str) -> Dict[str, Any]:
    lowered = str(message or "").lower()
    positive = sum(1 for token in POSITIVE_TOKENS if token in lowered)
    negative = sum(1 for token in NEGATIVE_TOKENS if token in lowered)
    if positive > negative:
        sentiment = "positive"
    elif negative > positive:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    score = max(-1.0, min(1.0, (positive - negative) / 3.0))
    return {"sentiment": sentiment, "score": round(score, 4)}


def _next_hint(observation: ToolObservation, state: Dict[str, Any]) -> str:
    if not observation.ok:
        return "recover_or_clarify"
    if observation.tool == "manage_cart" and not (state.get("cart") or {}).get("address"):
        return "collect_address"
    if observation.tool == "collect_address":
        return "checkout_order"
    if observation.tool in {"create_support_ticket", "resolve_support_ticket"}:
        return "support_followthrough"
    return "continue"
