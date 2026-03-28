from typing import Dict

from app.services.intelligence.customer_profile import get_customer_profile


def decide_strategy(user: Dict, message: str) -> Dict:
    profile = get_customer_profile(user["phone"], user["business_id"])
    segment = profile.get("segment", "new")

    msg = message.lower()

    # -------------------------
    # MODE DETECTION
    # -------------------------
    mode = "sales"

    if any(word in msg for word in ["order", "status", "delivery", "track"]):
        mode = "order"

    elif any(word in msg for word in ["problem", "issue", "help", "complaint", "refund"]):
        mode = "support"

    # -------------------------
    # BASE STRATEGY
    # -------------------------
    strategy = {
        "mode": mode,
        "tone": "normal",
        "goal": "assist",
        "priority": "normal",
        "upsell": False,
    }

    # -------------------------
    # MODE-BASED LOGIC
    # -------------------------
    if mode == "sales":
        strategy["goal"] = "maximize_revenue"

        if segment in ["repeat", "vip"]:
            strategy["upsell"] = True

    elif mode == "order":
        strategy["goal"] = "complete_order_flow"
        strategy["upsell"] = False  # 🚨 IMPORTANT

    elif mode == "support":
        strategy["goal"] = "resolve_issue_fast"
        strategy["tone"] = "empathetic"
        strategy["upsell"] = False  # 🚨 NEVER upsell here

    # -------------------------
    # SEGMENT ADJUSTMENTS
    # -------------------------
    if segment == "vip":
        strategy["tone"] = "premium"
        strategy["priority"] = "high"

    return strategy