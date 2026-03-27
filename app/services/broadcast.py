from typing import Dict, List

from app.services.llm import generate_marketing_message
from app.services.vendor import send_whatsapp_message


def create_broadcast_preview(request_text: str, targets: List[str], style: str) -> Dict[str, str]:
    message = generate_marketing_message(request_text, style)
    preview = (
        "Broadcast preview:\n"
        f"{message}\n"
        f"Recipients: {len(targets)}\n"
        "Reply YES to send or NO to cancel."
    )
    return {"message": message, "preview": preview}


def execute_broadcast(message: str, targets: List[str]) -> Dict[str, int]:
    sent = 0
    for target in targets:
        send_whatsapp_message(target, message)
        sent += 1
    return {"sent": sent}
