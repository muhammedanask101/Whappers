import json
from datetime import datetime, timezone
from typing import Any, Dict

from app.db.database import execute, fetch_one


def get_conversation_state(phone: str) -> Dict[str, Any]:
    row = fetch_one("SELECT * FROM conversation_state WHERE phone = ?", (phone,))
    if not row:
        return {"phone": phone, "pendingAction": None, "lastLanguage": "english"}

    pending_action = None
    if row["pending_type"]:
        pending_action = {
            "type": row["pending_type"],
            "data": json.loads(row["pending_data"] or "{}"),
        }
    return {
        "phone": row["phone"],
        "role": row["role"],
        "pendingAction": pending_action,
        "lastLanguage": row["last_language"] or "english",
        "lastMessage": row["last_message"],
    }


def set_pending_action(
    phone: str,
    *,
    role: str,
    action_type: str,
    data: Dict[str, Any],
    last_language: str,
    last_message: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    execute(
        """
        INSERT INTO conversation_state (phone, role, pending_type, pending_data, last_language, last_message, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            role = excluded.role,
            pending_type = excluded.pending_type,
            pending_data = excluded.pending_data,
            last_language = excluded.last_language,
            last_message = excluded.last_message,
            updated_at = excluded.updated_at
        """,
        (phone, role, action_type, json.dumps(data), last_language, last_message, now),
    )


def clear_pending_action(phone: str) -> None:
    execute(
        """
        UPDATE conversation_state
        SET pending_type = NULL, pending_data = NULL, updated_at = ?
        WHERE phone = ?
        """,
        (datetime.now(timezone.utc).isoformat(), phone),
    )


def update_last_language(phone: str, role: str, language: str, last_message: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    execute(
        """
        INSERT INTO conversation_state (phone, role, last_language, last_message, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(phone) DO UPDATE SET
            role = excluded.role,
            last_language = excluded.last_language,
            last_message = excluded.last_message,
            updated_at = excluded.updated_at
        """,
        (phone, role, language, last_message, now),
    )
