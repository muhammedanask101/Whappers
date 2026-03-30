from __future__ import annotations

from typing import Any


def success_response(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def error_response(message: str, data: Any = None) -> dict[str, Any]:
    return {"success": False, "data": data, "error": message}
