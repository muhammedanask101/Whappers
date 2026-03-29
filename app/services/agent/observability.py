from __future__ import annotations

from typing import Any, Dict, List

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now


def log_agent_trace(
    *,
    business_id: str,
    phone: str,
    role: str,
    message: str,
    analysis: Dict[str, Any],
    strategy: Dict[str, Any],
    goal_tracking: Dict[str, Any],
    decisions: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    reflections: List[Dict[str, Any]],
    metrics: Dict[str, Any],
) -> None:
    get_collection("agent_traces").insert_one(
        {
            "business_id": business_id or DEFAULT_BUSINESS_ID,
            "phone": phone,
            "role": role,
            "message": message,
            "analysis": analysis,
            "strategy": strategy,
            "goal_tracking": goal_tracking,
            "decisions": decisions,
            "tools": tools,
            "reflections": reflections,
            "metrics": metrics,
            "created_at": utc_now(),
        }
    )


def get_recent_traces(business_id: str, *, phone: str = "", limit: int = 25) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"business_id": business_id or DEFAULT_BUSINESS_ID}
    if phone:
        query["phone"] = phone
    return list(
        get_collection("agent_traces")
        .find(query, {"analysis": 1, "strategy": 1, "goal_tracking": 1, "metrics": 1, "created_at": 1, "message": 1, "phone": 1, "role": 1})
        .sort("created_at", -1)
        .limit(limit)
    )
