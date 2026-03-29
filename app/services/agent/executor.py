from __future__ import annotations

from typing import Any, Dict, Optional


def handle_pending_action(message: str, user: Dict[str, Any], state: Dict[str, Any]) -> Optional[str]:
    workflow = state.get("workflow") or {}
    stage = workflow.get("stage")
    cart = state.get("cart") or {}
    if stage == "collect_address" and cart.get("items"):
        text = message.strip()
        if len(text.split()) >= 3 and any(token in text.lower() for token in ["road", "street", "lane", "house", "near", "pin", "city", "district"]):
            return None
    return None
