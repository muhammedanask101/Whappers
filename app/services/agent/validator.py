from __future__ import annotations

from typing import Any, Dict, Tuple


def validate_action(action: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    if not action:
        return False, "Missing action"

    if action == "respond":
        return True, ""

    if action in {"inspect_customer_context", "inspect_order_context", "inspect_support_context", "inspect_business_context", "inspect_memory_context"}:
        return True, ""

    if action == "manage_cart":
        items = args.get("items")
        if items is not None and not isinstance(items, list):
            return False, "manage_cart.items must be a list"
        return True, ""

    if action == "collect_address":
        if "address" in args and not isinstance(args.get("address"), str):
            return False, "collect_address.address must be a string"
        return True, ""

    if action == "repair_order_context":
        return True, ""

    if action in {"checkout_order", "search_catalog", "get_product_details", "get_order_status"}:
        return True, ""

    if action == "vendor_update_order_status":
        status = str(args.get("status") or "").strip().lower()
        if status not in {"accepted", "rejected", "out_for_delivery", "delivered"}:
            return False, "Unsupported vendor status"
        return True, ""

    if action in {"create_support_ticket", "resolve_support_ticket", "recommend_products"}:
        return True, ""

    if action in {
        "manage_product",
        "update_business_profile",
        "create_offer",
        "create_promotion",
        "create_event",
        "send_broadcast",
    }:
        return True, ""

    return False, f"Unknown action: {action}"
