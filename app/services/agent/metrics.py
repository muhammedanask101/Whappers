from __future__ import annotations

from typing import Any, Dict

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now


def record_metric(
    business_id: str,
    *,
    metric: str,
    amount: float = 1.0,
    dimensions: Dict[str, Any] | None = None,
) -> None:
    get_collection("performance_metrics").insert_one(
        {
            "business_id": business_id or DEFAULT_BUSINESS_ID,
            "metric": metric,
            "amount": float(amount),
            "dimensions": dimensions or {},
            "created_at": utc_now(),
        }
    )


def record_agent_run_metrics(
    business_id: str,
    *,
    phone: str,
    intent: str,
    metrics: Dict[str, Any],
    goal_tracking: Dict[str, Any],
) -> None:
    base_dimensions = {
        "phone": phone,
        "intent": intent,
        "goal_status": goal_tracking.get("status", "active"),
        "goal_type": goal_tracking.get("goal_type", ""),
        "llm_tier": metrics.get("llm_tier", ""),
    }
    record_metric(business_id, metric="agent_runs", dimensions=base_dimensions)
    record_metric(business_id, metric="agent_steps", amount=float(metrics.get("steps", 0) or 0), dimensions=base_dimensions)
    record_metric(business_id, metric="agent_uncertainty", amount=float(metrics.get("uncertainty", 0.0) or 0.0), dimensions=base_dimensions)
    if goal_tracking.get("status") == "completed":
        record_metric(business_id, metric="goal_completed", dimensions=base_dimensions)
