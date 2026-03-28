from typing import Dict, Optional

from app.services.agent_legacy import (
    execute_tool,
    _handle_pending_action,
)

from app.services.intelligence.recommendations import get_upsell_suggestion


def execute_action(action: str, args: Dict, user: Dict, state: Dict, style: str, strategy) -> str:

    # 🔥 HARD RULES (SYSTEM CONTROL)

    # ❌ Prevent selling in support mode
    if strategy.get("mode") == "support" and action == "get_products":
        return "I understand your issue. Let me help resolve it first."

    # ❌ Prevent upsell in support
    if strategy.get("mode") == "support":
        strategy["upsell"] = False

    # Execute tool
    result = execute_tool(action, args, state, user, style, "")

    message = result.get("message") if isinstance(result, dict) else str(result)

    # 🔥 Smart upsell (only in correct context)
    if (
        action == "create_order"
        and strategy.get("upsell")
        and strategy.get("mode") == "sales"
    ):
        upsell = get_upsell_suggestion(
            args.get("items", []),
            user.get("business_id"),
        )

        if upsell:
            message += f"\n\n🔥 You might also like: {upsell['display_name']} for Rs {upsell['price']}"

    return message


def handle_pending_action(message: str, user: Dict, state: Dict) -> Optional[str]:
    return _handle_pending_action(
        message,
        user,
        state.get("lastLanguage", "english"),
        state
    )