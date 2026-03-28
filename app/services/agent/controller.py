from typing import Dict

from app.services.agent.memory import (
    get_state,
    save_turn,
    update_language,
)
from app.services.agent.main_agent import run_agent
from app.services.agent.executor import handle_pending_action
from app.services.intelligence.customer_profile import update_last_active
from app.services.intelligence.customer_profile import get_customer_profile


async def process_incoming_message(message: str, user: Dict[str, str]) -> str:
    business_id = user.get("business_id")

    # -------------------------
    # LOAD STATE
    # -------------------------
    state = get_state(user["phone"], business_id)

    profile = get_customer_profile(user["phone"], business_id)

    state["segment"] = profile.get("segment", "new")

    # -------------------------
    # HANDLE PENDING ACTIONS (KEEP THIS)
    # -------------------------
    pending_reply = handle_pending_action(message, user, state)
    if pending_reply:
        save_turn(user["phone"], message, pending_reply, business_id)
        return pending_reply

    # -------------------------
    # LANGUAGE + ACTIVITY (KEEP LIGHTWEIGHT)
    # -------------------------
    update_language(user, message, business_id)
    update_last_active(user["phone"], business_id)

    # -------------------------
    # 🧠 UNIFIED AGENT (CORE)
    # -------------------------
    reply = await run_agent(
        user_message=message,
        user=user,
        state=state,
    )

    # -------------------------
    # SAVE MEMORY
    # -------------------------
    save_turn(user["phone"], message, reply, business_id)

    return reply