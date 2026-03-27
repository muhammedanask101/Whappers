from typing import Dict, List

from app.services.vendor import send_whatsapp_message


def create_broadcast_preview(message: str, targets: List[str]) -> Dict[str, str]:
    preview = "Broadcast preview:\n" f"{message}\n" f"Recipients: {len(targets)}"
    return {"message": message, "preview": preview}


def execute_broadcast(message: str, targets: List[str]) -> Dict[str, int]:
    sent = 0
    for target in targets:
        send_whatsapp_message(target, message)
        sent += 1
    return {"sent": sent}
