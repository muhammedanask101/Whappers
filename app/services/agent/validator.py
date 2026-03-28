from typing import Dict, Tuple


def validate_action(action: str, args: Dict) -> Tuple[bool, str]:
    """
    Validate tool calls before execution
    """

    # -------------------------
    # CREATE ORDER VALIDATION
    # -------------------------
    if action == "create_order":
        if "items" not in args:
            return False, "Missing items"

        if not isinstance(args["items"], list) or len(args["items"]) == 0:
            return False, "Items must be a non-empty list"

        for item in args["items"]:
            if "product_id" not in item:
                return False, "Missing product_id"

            if "quantity" not in item:
                return False, "Missing quantity"

            if not isinstance(item["quantity"], int) or item["quantity"] <= 0:
                return False, "Invalid quantity"

    # -------------------------
    # ORDER STATUS VALIDATION
    # -------------------------
    if action == "get_order_status":
        if "order_id" in args and not isinstance(args["order_id"], str):
            return False, "Invalid order_id"

    # -------------------------
    # DEFAULT PASS
    # -------------------------
    return True, ""