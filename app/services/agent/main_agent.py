from typing import Dict, Any, List


from app.services.llm import call_llm
from app.services.agent_legacy import _handle_pending_action, execute_tool
from app.services.agent.validator import validate_action
import re
import json

def extract_ticket_id(message: str) -> str:
    """
    Extracts ticket ID like SUP_123 or SUP-123 from text
    """
    match = re.search(r"(SUP[_-]?\d+)", message.upper())
    return match.group(1) if match else ""


MAX_STEPS = 5

TOOLS_SCHEMA = """
Available tools:

1. get_products
2. get_product_details
3. get_business_info
4. create_order
5. get_order_status
6. update_order_status
7. create_support_ticket
8. resolve_support_ticket
9. add_product
10. update_product
11. delete_product
12. create_offer
13. create_promotion
14. create_event
15. update_business_info
16. send_broadcast
17. respond
"""


async def run_agent(user_message: str, user: Dict, state: Dict) -> str:
    """
    Unified Agent Loop (ReAct-style)
    """

    scratchpad: List[Dict[str, Any]] = []

    # -------------------------
# 🚨 VENDOR STATUS HANDLING (INSERT HERE)
# -------------------------
    if user.get("role") == "vendor":

        from app.services.agent_legacy import _infer_vendor_status
        from app.services.order import get_latest_order

        status = _infer_vendor_status(user_message)

        if status:
            scratchpad.append({
                "step": "system",
                "action": "vendor_status_detected",
                "arguments": {"status": status},
                "result": f"Vendor indicated status: {status}",
                "status": "info"
            })


    for step in range(MAX_STEPS):

    # -------------------------
    # 🔥 FORCE FLOW COMPLETION (ADDRESS / PENDING STATE)
    # -------------------------
        if state.get("pending_address"):
            pending = state["pending_address"]

            scratchpad.append({
                "step": "system",
                "action": "pending_address",
                "arguments": {},
                "result": f"User must provide address for order {pending.get('order_id')}",
                "status": "info"
            })

        # -------------------------
        # BUILD PROMPT
        # -------------------------
        prompt = build_prompt(
            user_message=user_message,
            state=state,
            scratchpad=scratchpad,
            user=user
        )

        pending = state.get("pendingAction")

        if pending and pending.get("type") == "draft_tool":
            return _handle_pending_action(user_message, user, state.get("lastLanguage"), state)

        if user["role"] == "vendor":
            msg = user_message.lower()

            ticket_id = extract_ticket_id(msg)

            if not ticket_id:
                return "Please provide a valid ticket ID (e.g., SUP_123)."

            result = execute_tool(
                "resolve_support_ticket",
                {"ticket_id": ticket_id},
                state,
                user,
                state.get("lastLanguage", "english"),
                ""
            )

            return result.get("message", "Done.")

        decision_raw = call_llm(prompt, json_mode=True, temperature=0.2)

        # DEBUG (VERY IMPORTANT)
        print("LLM RAW:", decision_raw)

        try:
            if isinstance(decision_raw, str):
                decision = json.loads(decision_raw)
            else:
                decision = decision_raw
        except Exception:
            return "Sorry, I had trouble understanding that. Can you rephrase?"

        action = decision.get("action")
        args = decision.get("arguments", {})
        message = decision.get("message", "")

        # ✅ FINAL RESPONSE
        if action == "respond":
            return message or "Okay 👍"

        # ⚠️ SAFETY
        if not action:
            return "Sorry, I didn't understand that. Can you rephrase?"


        is_valid, error = validate_action(action, args)
        # 🔧 EXECUTE TOOL
        if not is_valid:
            result = f"Validation failed: {error}"
        else:
            try:
                result = execute_tool(
                    action,
                    args,
                    state,
                    user,
                    style="english",   # or dynamic later
                    llm_message=message
                )
            except Exception as e:
                result = f"Tool execution failed: {str(e)}"
        status = "error" if isinstance(result, str) and "failed" in result.lower() else "success"
        # 🧠 ADD OBSERVATION (THIS IS WHAT YOU WERE MISSING)
        scratchpad.append({
            "step": step + 1,
            "action": action,
            "arguments": args,
            "result": result,
            "status": status
        })

        # 🚨 If tool returned useful data → respond immediately
        if action in ["get_products", "get_product_details", "get_order_status"]:
            return str(result)

    return "Something went wrong. Please try again."


def build_prompt(user_message: str, state: Dict, scratchpad: List[Dict], user: Dict) -> List[Dict]:
    pending_instruction = ""

    if state.get("pending_address"):
        pending = state["pending_address"]
        pending_instruction = f"""
    IMPORTANT:
    User has an incomplete order (order_id={pending.get('order_id')}).
    You MUST collect delivery address before doing anything else.
    Do NOT suggest products or restart flow.
    """
    """
    Builds LLM messages with memory + reasoning context
    """

    system_prompt = f"""
You are a WhatsApp AI agent for a business.

Your goals:
- Help users discover and buy products
- Handle orders and tracking
- Solve customer support issues
- Increase revenue when appropriate

You can use tools.

Rules:
- Think step by step
- Use tools when needed
- If you have enough info → respond
- If not → ask clarification
- Be natural, like WhatsApp chat
- Keep answers concise

If a tool fails:
- Analyze why it failed
- Try a better action OR fix arguments
- Do NOT repeat the same mistake

{pending_instruction}

{TOOLS_SCHEMA}

If a tool returns "needs_input":
- Ask the user for the missing field
- Wait for user input
- Then call the appropriate tool to complete the action


- If user is VIP → prioritize premium products
- If user is new → guide gently
- If user is inactive → encourage ordering
- If user is buying → suggest add-ons
- If support issue → resolve first, do not sell

If a support ticket exists:
- Identify the ticket ID
- Use resolve_support_ticket tool
- Confirm resolution clearly

Always balance helpfulness with revenue generation.

Customer Info:
- Phone: {user.get("phone")}
- Segment: {state.get("segment", "unknown")}

Previous steps:
{format_scratchpad(scratchpad)}

Respond ONLY in JSON:
{{
  "action": "...",
  "arguments": {{}},
  "message": "..."
}}
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]


def format_scratchpad(scratchpad: List[Dict]) -> str:
    if not scratchpad:
        return "None"

    lines = []
    for step in scratchpad:
        lines.append(
            f"Step {step['step']}: Action={step['action']} Result={step['result']}"
        )

    return "\n".join(lines)