from __future__ import annotations

from typing import Any, Dict

from app.services.agent.policy import analyze_message, build_strategy
from app.services.agent.learning import get_learning_profile
from app.services.intelligence.customer_profile import get_customer_profile


def decide_strategy(user: Dict[str, Any], message: str, state: Dict[str, Any] | None = None) -> Dict[str, Any]:
    state = state or {}
    profile = get_customer_profile(user["phone"], user["business_id"])
    learning = get_learning_profile(user["phone"], user["business_id"])
    analysis = analyze_message(message, user=user, state=state, learning_profile=learning)
    strategy = build_strategy(user=user, profile=profile, analysis=analysis, state=state)
    strategy["intent"] = analysis["intent"]
    strategy["confidence"] = analysis["confidence"]
    return strategy
