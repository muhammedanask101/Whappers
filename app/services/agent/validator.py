from __future__ import annotations

from typing import Any, Dict, Tuple


def validate_action(action: str, args: Dict[str, Any]) -> Tuple[bool, str]:
    if not action:
        return False, "Missing action"
    if action == "respond":
        return True, ""
    if action in {
        "inspect_customer_context",
        "inspect_order_context",
        "inspect_support_context",
        "inspect_business_context",
        "inspect_memory_context",
        "repair_order_context",
        "checkout_order",
        "search_catalog",
        "get_product_details",
        "get_order_status",
        "create_support_ticket",
        "resolve_support_ticket",
        "recommend_products",
        "manage_product",
        "update_business_profile",
        "create_offer",
        "create_promotion",
        "create_event",
        "send_broadcast",
        "summarize_business",
        "summarize_sales",
        "get_top_products",
        "get_business_health_metrics",
        "list_orders",
    }:
        return True, ""
    if action == "filter_orders":
        return isinstance(args.get("status", ""), str), "filter_orders.status must be a string"
    if action == "manage_cart":
        operation = str(args.get("operation") or "").strip().lower()
        if operation and operation not in {"add", "remove", "set", "view", "clear"}:
            return False, "manage_cart.operation is invalid"
        items = args.get("items")
        if items is not None and not isinstance(items, list):
            return False, "manage_cart.items must be a list"
        for item in items or []:
            if not isinstance(item, dict):
                return False, "manage_cart.items entries must be objects"
            if not str(item.get("product_name") or item.get("name") or "").strip():
                return False, "manage_cart item requires product_name"
        return True, ""
    if action == "collect_address":
        if "address" in args and not isinstance(args.get("address"), str):
            return False, "collect_address.address must be a string"
        return True, ""
    if action in {"vendor_update_order_status", "update_order_status"}:
        status = str(args.get("status") or "").strip().lower()
        if status not in {"accepted", "rejected", "out_for_delivery", "delivered"}:
            return False, "Unsupported vendor status"
        return True, ""
    if action == "bulk_update_orders":
        if not isinstance(args.get("criteria") or {}, dict):
            return False, "bulk_update_orders.criteria must be an object"
        if not isinstance(args.get("action") or {}, dict):
            return False, "bulk_update_orders.action must be an object"
        return True, ""
    return False, f"Unknown action: {action}"
