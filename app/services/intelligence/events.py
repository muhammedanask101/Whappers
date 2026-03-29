from __future__ import annotations

from typing import Any, Dict

from app.services.agent.learning import record_event


def trigger_event(event_type: str, data: Dict[str, Any]) -> None:
    business_id = str(data.get("business_id") or "")
    phone = str(data.get("phone") or "")
    if not business_id:
        return
    record_event(event_type, business_id=business_id, phone=phone, payload=data)
