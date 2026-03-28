import json
from typing import Any, Dict, List, Optional

from click import style
from datetime import datetime

from app import db
from app.db.mongo import DEFAULT_BUSINESS_ID
from app.services.broadcast import execute_broadcast
from app.services.llm import detect_language_style, is_confirmation_no, is_confirmation_yes
from app.services.order import build_order_preview, create_order, get_latest_order, get_latest_vendor_actionable_order, update_order_address, update_order_status
from app.services.state import append_history, clear_pending_action, get_conversation_state, get_recent_history, set_pending_action, update_last_language
from app.services.vendor import (
    add_product,
    create_event,
    create_promotion,
    create_support_ticket,
    delete_product,
    format_offer_preview,
    generate_vendor_acknowledgement,
    get_business_profile,
    get_customer_broadcast_targets,
    get_product_catalog,
    get_product_catalog_map,
    get_product_category,
    get_product_details,
    normalize_product_name,
    notify_customer_order_update,
    send_vendor_order_notification,
    send_whatsapp_message,
    update_business_profile,
    update_product,
    update_product_offer,
)


ALLOWED_ORDER_STATUSES = {"accepted", "rejected", "out_for_delivery", "delivered"}
TOOL_NAMES = {
    "get_products",
    "get_product_details",
    "get_business_info",
    "create_order",
    "get_order_status",
    "create_support_ticket",
    "update_order_status",
    "add_product",
    "update_product",
    "delete_product",
    "create_offer",
    "create_promotion",
    "create_event",
    "update_business_info",
    "send_broadcast",
    "resolve_support_ticket",
}
VENDOR_ONLY_TOOLS = {
    "update_order_status",
    "add_product",
    "update_product",
    "delete_product",
    "create_offer",
    "create_promotion",
    "create_event",
    "update_business_info",
    "send_broadcast",
    "resolve_support_ticket",
}



def _fallback_response(message: str, user: Dict[str, str], style: str, state: Dict[str, Any]) -> str:
    if user.get("role") == "vendor":
        return "I can manage products, offers, promotions, business details, broadcasts, and order status. Tell me the exact change you want."

    stripped = message.strip()
    if any(char.isdigit() for char in stripped):
        items = _extract_basic_items(stripped)
        if items:
            validated = validate_and_fix_args("create_order", {"items": items}, state, message, user)
            result = execute_tool("create_order", validated, state, user, style, "")
            if result.get("message"):
                return result["message"]

    if 0 < len(stripped.split()) <= 2:
        if style == "manglish":
            return "Hi, njan sahayikkam. Enthanu vendath?"
        if style == "malayalam":
            return "ഹലോ, ഞാൻ സഹായിക്കാം. എന്താണ് വേണ്ടത്?"
        return "Hi, I can help. What would you like?"

    args = validate_and_fix_args("get_products", {"search": message}, state, message, user)
    result = execute_tool("get_products", args, state, user, style, "")
    if result.get("message"):
        return result["message"]

    if style == "manglish":
        return "Njan productsum ordersum supportum handle cheyyam. Kurachu details parayu."
    if style == "malayalam":
        return "Products, orders, support എന്നിവയിൽ ഞാൻ സഹായിക്കാം. കൂടുതൽ വിശദമായി പറയൂ."
    return "I can help with products, orders, and support. Tell me a bit more."

def resolve_ticket(ticket_id: str, resolution: str):
    from app.db.mongo import get_db

    db = get_db()

    result = db.support_tickets.update_one(
        {"ticket_id": ticket_id},
        {
            "$set": {
                "status": "resolved",
                "resolution_notes": resolution,
                "resolved_at": datetime.utcnow(),
            }
        }
    )

    return result.modified_count > 0

def _tool_resolve_support_ticket(args: Dict[str, Any], user: Dict[str, str], state: Dict[str, Any]) -> Dict[str, str]:
    ticket_id = args.get("ticket_id")
    resolution = args.get("resolution", "Issue resolved")

    if not ticket_id:
        return {"status": "error", "message": "Missing ticket_id"}

    ticket = db.support_tickets.find_one({"ticket_id": ticket_id})

    if not ticket:
        return {"status": "error", "message": f"Ticket {ticket_id} not found"}

    db.support_tickets.update_one(
        {"ticket_id": ticket_id},
        {
            "$set": {
                "status": "resolved",
                "resolution_notes": resolution,
                "resolved_at": datetime.utcnow()
            }
        }
    )

    # Notify customer
    send_whatsapp_message(
        ticket["customer_phone"],
        f"✅ Your issue has been resolved: {resolution}"
    )

    return {
        "status": "success",
        "message": f"Ticket {ticket_id} resolved successfully"
    }

def validate_and_fix_args(action: str, args: Dict[str, Any], state: Dict[str, Any], message: str, user: Dict[str, str]) -> Dict[str, Any]:
    safe_args = dict(args or {})
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID

    if action == "create_order":
        draft = _get_draft_data(state)
        draft_items = draft.get("items", [])
        draft_address = str(draft.get("address") or "").strip()
        raw_items = list(safe_args.get("items", []) or [])
        single_name = normalize_product_name(
            str(
                safe_args.get("product_name")
                or safe_args.get("product")
                or safe_args.get("name")
                or safe_args.get("item")
                or ""
            ).strip()
        )
        if single_name:
            raw_items.append(
                {
                    "name": single_name,
                    "qty": safe_args.get("quantity", safe_args.get("qty", 1)),
                }
            )
        normalized_items = []
        for item in raw_items:
            name = normalize_product_name(
                str(
                    item.get("name")
                    or item.get("product")
                    or item.get("item")
                    or item.get("title")
                    or ""
                ).strip()
            )
            qty = _safe_int(
                item.get("qty", item.get("quantity", item.get("count", item.get("units", 1)))),
                default=1,
            )
            if name and product_exists(name, business_id=business_id):
                normalized_items.append({"name": name, "qty": qty})
        operation = str(safe_args.get("operation") or safe_args.get("mode") or "add").strip().lower()
        if operation == "get":
            return {"items": draft_items, "address": draft_address, "operation": "get", "business_id": business_id}
        if operation not in {"add", "replace", "remove"}:
            operation = "add"
        merged = apply_cart_operation(draft_items, normalized_items, operation)
        address = str(safe_args.get("address") or "").strip() or draft_address
        if not address and is_address(message) and draft_items and not normalized_items:
            address = message.strip()
        return {"items": merged, "address": address, "operation": operation, "business_id": business_id}

    if action == "get_products":
        requested_category = str(safe_args.get("filter_category") or "").strip().lower() or None
        search = str(safe_args.get("search") or "").strip() or None
        if not requested_category:
            requested_category = infer_category_from_message(message)
        sort_by = str(safe_args.get("sort_by") or "").strip().lower() or None
        if sort_by not in {"display_name", "price", "category", "updated_at", None}:
            sort_by = None
        sort_order = str(safe_args.get("sort_order") or "asc").strip().lower() or "asc"
        if sort_order not in {"asc", "desc"}:
            sort_order = "asc"
        tags = safe_args.get("tags")
        if tags is not None and not isinstance(tags, list):
            tags = None
        min_price = _safe_float(safe_args.get("min_price"))
        max_price = _safe_float(safe_args.get("max_price"))
        limit = _safe_int(safe_args.get("limit", 10), default=10)
        return {
            "filter_category": requested_category,
            "search": search,
            "sort_by": sort_by or "display_name",
            "sort_order": sort_order,
            "min_price": min_price,
            "max_price": max_price,
            "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()] if tags is not None else None,
            "limit": min(max(limit, 1), 20),
            "business_id": business_id,
        }

    if action == "get_product_details":
        name = normalize_product_name(str(safe_args.get("name") or safe_args.get("product") or "").strip())
        return {"name": name or normalize_product_name(message), "business_id": business_id}

    if action == "get_business_info":
        return {"topic": str(safe_args.get("topic") or "").strip().lower() or None, "business_id": business_id}

    if action == "get_order_status":
        return {"phone": user["phone"], "context": message, "business_id": business_id}

    if action == "create_support_ticket":
        return {
            "customer_phone": user["phone"],
            "issue": str(safe_args.get("issue") or message).strip(),
            "order_id": str(safe_args.get("order_id") or safe_args.get("orderId") or "").strip().upper() or None,
            "preferred_contact": str(safe_args.get("preferred_contact") or "").strip() or None,
            "business_id": business_id,
        }

    if action == "resolve_support_ticket":
        return {
            "ticket_id": str(safe_args.get("ticket_id") or "").strip(),
            "resolution": str(safe_args.get("resolution") or "").strip() or "Issue resolved",
            "business_id": business_id
        }

    if action == "update_order_status":
        status = str(safe_args.get("status") or "").strip().lower()
        order_id = str(safe_args.get("order_id") or safe_args.get("orderId") or "").strip().upper() or None
        if status not in ALLOWED_ORDER_STATUSES:
            status = ""
        return {"status": status, "order_id": order_id, "business_id": business_id}

    if action == "add_product":
        tags = safe_args.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        attributes = safe_args.get("attributes", {})
        if not isinstance(attributes, dict):
            attributes = {}
        return {
            "name": normalize_product_name(str(safe_args.get("name") or "").strip()),
            "price": _safe_float(safe_args.get("price")),
            "unit": str(safe_args.get("unit") or "unit").strip() or "unit",
            "category": str(safe_args.get("category") or "").strip().lower() or None,
            "description": str(safe_args.get("description") or "").strip(),
            "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()],
            "attributes": attributes,
            "business_id": business_id,
        }

    if action == "update_product":
        tags = safe_args.get("tags")
        if tags is not None and not isinstance(tags, list):
            tags = None
        attributes = safe_args.get("attributes")
        if attributes is not None and not isinstance(attributes, dict):
            attributes = None
        active = safe_args.get("active")
        if active is not None:
            active = bool(active)
        return {
            "name": normalize_product_name(str(safe_args.get("name") or "").strip()),
            "price": _safe_float(safe_args.get("price")),
            "unit": str(safe_args.get("unit")).strip() if safe_args.get("unit") is not None else None,
            "category": str(safe_args.get("category")).strip().lower() if safe_args.get("category") is not None else None,
            "description": str(safe_args.get("description")).strip() if safe_args.get("description") is not None else None,
            "tags": [str(tag).strip().lower() for tag in tags if str(tag).strip()] if tags is not None else None,
            "attributes": attributes,
            "active": active,
            "business_id": business_id,
        }

    if action == "delete_product":
        return {"name": normalize_product_name(str(safe_args.get("name") or "").strip()), "business_id": business_id}

    if action == "create_offer":
        product = normalize_product_name(str(safe_args.get("product") or "").strip())
        if not product_exists(product, business_id=business_id):
            product = ""
        return {"product": product, "price": _safe_float(safe_args.get("price")), "date": str(safe_args.get("date") or "").strip() or None, "business_id": business_id}

    if action == "create_promotion":
        scope = str(safe_args.get("scope") or "").strip().lower() or "global"
        if scope not in {"global", "category", "product"}:
            scope = "global"
        target = str(safe_args.get("target") or "").strip().lower() or None
        if scope == "product" and target:
            target = normalize_product_name(target)
            if not product_exists(target, business_id=business_id):
                target = ""
        return {
            "title": str(safe_args.get("title") or "").strip(),
            "scope": scope,
            "target": target,
            "discount_type": str(safe_args.get("discount_type") or "").strip().lower() or "percent",
            "discount_value": _safe_float(safe_args.get("discount_value")),
            "starts_at": str(safe_args.get("starts_at") or "").strip() or None,
            "ends_at": str(safe_args.get("ends_at") or "").strip() or None,
            "broadcast_text": str(safe_args.get("broadcast_text") or "").strip() or None,
            "business_id": business_id,
        }

    if action == "create_event":
        promotion_scope = str(safe_args.get("promotion_scope") or "").strip().lower() or None
        promotion_target = str(safe_args.get("promotion_target") or "").strip().lower() or None
        if promotion_scope == "product" and promotion_target:
            promotion_target = normalize_product_name(promotion_target)
            if not product_exists(promotion_target, business_id=business_id):
                promotion_target = ""
        return {
            "title": str(safe_args.get("title") or "").strip(),
            "description": str(safe_args.get("description") or "").strip(),
            "starts_at": str(safe_args.get("starts_at") or "").strip() or None,
            "ends_at": str(safe_args.get("ends_at") or "").strip() or None,
            "promotion_scope": promotion_scope,
            "promotion_target": promotion_target,
            "discount_type": str(safe_args.get("discount_type") or "").strip().lower() or None,
            "discount_value": _safe_float(safe_args.get("discount_value")),
            "broadcast_text": str(safe_args.get("broadcast_text") or "").strip() or None,
            "business_id": business_id,
        }

    if action == "update_business_info":
        groups = safe_args.get("broadcast_groups")
        if groups is not None and not isinstance(groups, list):
            groups = None
        return {
            "name": str(safe_args.get("name") or "").strip() or None,
            "business_type": str(safe_args.get("business_type") or "").strip().lower() or None,
            "description": str(safe_args.get("description") or "").strip() or None,
            "address": str(safe_args.get("address") or "").strip() or None,
            "hours": str(safe_args.get("hours") or "").strip() or None,
            "contact_phone": str(safe_args.get("contact_phone") or "").strip() or None,
            "support_details": str(safe_args.get("support_details") or "").strip() or None,
            "policies": str(safe_args.get("policies") or "").strip() or None,
            "delivery_info": str(safe_args.get("delivery_info") or "").strip() or None,
            "broadcast_groups": [str(item).strip() for item in groups if str(item).strip()] if groups is not None else None,
            "business_id": business_id,
        }

    if action == "send_broadcast":
        return {"message": str(safe_args.get("message") or "").strip(), "business_id": business_id}

    safe_args.setdefault("business_id", business_id)
    return safe_args


def execute_tool(action: str, args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, str], style: str, llm_message: str) -> Dict[str, str]:
    if action in VENDOR_ONLY_TOOLS and user.get("role") != "vendor":
        return {"message": "I can help you browse products, place orders, and get support here. Store management actions are only available to the vendor account."}

    if action == "resolve_support_ticket":
        return _tool_resolve_support_ticket(args, user, state)
    if action == "get_products":
        return _tool_get_products(args, style)
    if action == "get_product_details":
        return _tool_get_product_details(args, style)
    if action == "get_business_info":
        return _tool_get_business_info(args, style)
    if action == "create_order":
        return _tool_create_order(args, user, style)
    if action == "get_order_status":
        return _tool_get_order_status(args, style)
    if action == "create_support_ticket":
        return _tool_create_support_ticket(args, style)
    if action == "update_order_status":
        return _tool_update_order_status(args, style)
    if action == "add_product":
        return _tool_add_product(args, user, style)
    if action == "update_product":
        return _tool_update_product(args, user, style)
    if action == "delete_product":
        return _tool_delete_product(args, user, style)
    if action == "create_offer":
        return _tool_create_offer(args, user, style)
    if action == "create_promotion":
        return _tool_create_promotion(args, user, style)
    if action == "create_event":
        return _tool_create_event(args, user, style)
    if action == "update_business_info":
        return _tool_update_business_info(args, user, style)
    if action == "send_broadcast":
        return _tool_send_broadcast(args, user, style)
    return {"message": llm_message or "Sorry, I didn’t get that. Can you rephrase?"}



def _handle_pending_action(message: str, user: Dict[str, str], style: str, state: Dict[str, Any]) -> Optional[str]:
    pending = state.get("pendingAction")
    if not pending:
        return None
    if pending.get("type") == "confirm_tool":
        if is_confirmation_no(message):
            clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
            return _localized_cancel(style)
        if not is_confirmation_yes(message):
            return None
        data = pending.get("data", {})
        tool = data.get("tool")
        arguments = data.get("arguments", {})
        if tool == "create_order":
            return _finalize_confirmed_order(arguments, user, style)
        if tool == "add_product":
            return _finalize_confirmed_add_product(arguments, user)
        if tool == "update_product":
            return _finalize_confirmed_update_product(arguments, user)
        if tool == "delete_product":
            return _finalize_confirmed_delete_product(arguments, user)
        if tool == "create_offer":
            return _finalize_confirmed_offer(arguments, user)
        if tool == "create_promotion":
            return _finalize_confirmed_promotion(arguments, user)
        if tool == "create_event":
            return _finalize_confirmed_event(arguments, user)
        if tool == "update_business_info":
            return _finalize_confirmed_business_info(arguments, user)
        if tool == "send_broadcast":
            return _finalize_confirmed_broadcast(arguments, user)
        clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
        return "Sorry, I didn’t get that. Can you rephrase?"
    if pending.get("type") == "draft_tool":
        if is_confirmation_no(message):
            clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
            return _localized_cancel(style)
        draft_tool = pending.get("data", {}).get("tool")
        if draft_tool == "create_order":
            return _handle_order_draft_progress(message, user, style, state)
    return None


def _tool_get_products(args: Dict[str, Any], style: str) -> Dict[str, str]:
    products = get_product_catalog(
        business_id=args.get("business_id") or DEFAULT_BUSINESS_ID,
        filter_category=args.get("filter_category"),
        search=args.get("search"),
        min_price=args.get("min_price"),
        max_price=args.get("max_price"),
        tags=args.get("tags"),
        sort_by=args.get("sort_by") or "display_name",
        sort_order=args.get("sort_order") or "asc",
        limit=args.get("limit") or 10,
    )
    return {"message": format_product_response(products, style, filters=args)}


def _tool_get_product_details(args: Dict[str, Any], style: str) -> Dict[str, str]:
    business_id = args.get("business_id") or DEFAULT_BUSINESS_ID
    product = get_product_details(args.get("name", ""), business_id=business_id)
    if not product:
        fallback = get_product_catalog(business_id=business_id, search=args.get("name"))
        return {"message": format_product_response(fallback, style, intro="I could not find an exact match. Here are the closest options:")}
    return {"message": format_product_detail_response(product, style)}


def _tool_get_business_info(args: Dict[str, Any], style: str) -> Dict[str, str]:
    return {"message": format_business_info_response(get_business_profile(args.get("business_id") or DEFAULT_BUSINESS_ID), style, args.get("topic"))}


def _tool_create_order(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    items = args.get("items", [])
    address = str(args.get("address") or "").strip()
    operation = str(args.get("operation") or "add").strip().lower()
    if operation == "get" and items:
        return {"message": _draft_message(items, style, operation="add")}
    if not items:
        return {"message": format_product_response(get_product_catalog(business_id=business_id), style, intro="I can help with these products:")}
    if not address:
        save_pending_draft(
            user,
            "create_order",
            style,
            _summarize_items(items),
            {"items": items, "address": ""}
        )
    if not args.get("address"):
        return {
            "status": "needs_input",
            "missing": "address",
            "message": "Please provide delivery address"
        }

    # 🚨 FORCE address collection (strong UX)
    if style == "manglish":
        return {"message": f"{_draft_message(items, style)}\n\n📍 Delivery address ayakkamo?"}
    if style == "malayalam":
        return {"message": f"{_draft_message(items, style)}\n\n📍 Delivery address അയക്കാമോ?"}

    _queue_confirmation(user, style, "create_order", {"items": items, "address": address}, address)
    return {"message": build_order_preview(items, address, style=style, business_id=business_id)["message"]}


def _tool_get_order_status(args: Dict[str, Any], style: str) -> Dict[str, str]:
    order = get_latest_order(args.get("phone", ""), business_id=args.get("business_id") or DEFAULT_BUSINESS_ID)
    if not order:
        return {"message": _localized_missing_order(style)}
    status = order.status.replace("_", " ")
    if style == "manglish":
        return {"message": f"Njan check cheythu. Order {order.orderId} ippo {status} aanu. Delay undengil njan help cheyyam."}
    if style == "malayalam":
        return {"message": f"ഞാൻ check ചെയ്തു. Order {order.orderId} ഇപ്പോൾ {status} ആണ്. Delay ഉണ്ടെങ്കിൽ ഞാൻ സഹായിക്കാം."}
    return {"message": f"I checked your order. Order {order.orderId} is currently {status}. If there’s a delay or issue, I can help you follow up."}


def _tool_create_support_ticket(args: Dict[str, Any], style: str) -> Dict[str, str]:
    issue = str(args.get("issue") or "").strip()
    if not issue:
        return {"message": "Tell me what went wrong and I’ll log it for the vendor right away."}
    business_id = args.get("business_id") or DEFAULT_BUSINESS_ID
    ticket = create_support_ticket(business_id=business_id, customer_phone=args.get("customer_phone", ""), issue=issue, order_id=args.get("order_id"), preferred_contact=args.get("preferred_contact"))
    contact_phone = get_business_profile(business_id).get("contact_phone") or "the vendor"
    if style == "manglish":
        return {"message": f"Njan support ticket {ticket['ticket_id']} create cheythu. Vendorine notify cheythittundu. Direct contact venel {contact_phone} use cheyyam."}
    if style == "malayalam":
        return {"message": f"Support ticket {ticket['ticket_id']} ഞാൻ create ചെയ്തു. Vendor-നെ notify ചെയ്തിട്ടുണ്ട്. നേരിട്ട് ബന്ധപ്പെടാൻ {contact_phone} ഉപയോഗിക്കാം."}
    return {"message": f"I’ve created support ticket {ticket['ticket_id']} and notified the vendor. If needed, you can also contact {contact_phone} directly."}


def _tool_update_order_status(args: Dict[str, Any], style: str) -> Dict[str, str]:
    business_id = args.get("business_id") or DEFAULT_BUSINESS_ID
    status = args.get("status")
    if status not in ALLOWED_ORDER_STATUSES:
        return {"message": "I need a valid order status like accepted, rejected, out for delivery, or delivered."}
    target = get_latest_vendor_actionable_order(target_order_id=args.get("order_id"), desired_status=status, business_id=business_id)
    if not target:
        return {"message": "I couldn’t find the matching order to update."}
    updated = update_order_status(target.orderId, status, business_id=business_id)
    if not updated:
        return {"message": "I couldn’t update the order right now."}
    customer_state = get_conversation_state(updated.customerPhone, business_id=business_id)
    notify_customer_order_update(updated.customerPhone, updated, customer_state.get("lastLanguage", "english"), business_id=business_id)
    return {"message": generate_vendor_acknowledgement(updated, style, updated.status)}


def _tool_add_product(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    if not args.get("name") or args.get("price") is None:
        return {"message": "Tell me the product name and price, and I’ll prepare it for you."}
    _queue_confirmation(user, style, "add_product", args, args.get("name", ""))
    return {"message": _format_product_change_preview("add", args)}


def _tool_update_product(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    if not args.get("name"):
        return {"message": "Tell me which product you want to update."}
    if not product_exists(args.get("name", ""), business_id=user.get("business_id") or DEFAULT_BUSINESS_ID):
        return {"message": "I couldn’t find that product in the catalog."}
    _queue_confirmation(user, style, "update_product", args, args.get("name", ""))
    return {"message": _format_product_change_preview("update", args)}


def _tool_delete_product(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    if not args.get("name"):
        return {"message": "Tell me which product you want removed from the catalog."}
    if not product_exists(args.get("name", ""), business_id=user.get("business_id") or DEFAULT_BUSINESS_ID):
        return {"message": "I couldn’t find that product in the catalog."}
    _queue_confirmation(user, style, "delete_product", args, args.get("name", ""))
    return {"message": f"Product removal preview:\nRemove {str(args.get('name', '')).title()} from the catalog.\nReply YES to confirm or NO to cancel."}


def _tool_create_offer(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    if not args.get("product") or args.get("price") is None:
        return {"message": "Tell me the product and offer price, and I’ll prepare it for you."}
    _queue_confirmation(user, style, "create_offer", args, args.get("product", ""))
    return {"message": format_offer_preview(args, style)}


def _tool_create_promotion(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    if not args.get("title") or args.get("discount_value") is None:
        return {"message": "Tell me the promotion title and discount, and I’ll prepare it for you."}
    _queue_confirmation(user, style, "create_promotion", args, args.get("title", ""))
    return {"message": _format_promotion_preview(args)}


def _tool_create_event(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    if not args.get("title") or not args.get("description"):
        return {"message": "Tell me the event title and description, and I’ll prepare it for you."}
    _queue_confirmation(user, style, "create_event", args, args.get("title", ""))
    return {"message": _format_event_preview(args)}


def _tool_update_business_info(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    preview = _format_business_profile_preview(args)
    if "No valid changes" in preview:
        return {"message": "Tell me what business details you want to update, such as address, hours, policies, support details, or broadcast groups."}
    _queue_confirmation(user, style, "update_business_info", args, "business profile")
    return {"message": preview}


def _tool_send_broadcast(args: Dict[str, Any], user: Dict[str, str], style: str) -> Dict[str, str]:
    message = str(args.get("message") or "").strip()
    if not message:
        return {"message": "Tell me the message you want to broadcast, and I’ll prepare it."}
    _queue_confirmation(user, style, "send_broadcast", {"message": message}, message)
    targets = get_customer_broadcast_targets(user.get("business_id") or DEFAULT_BUSINESS_ID)
    return {"message": f"Broadcast preview:\n{message}\nRecipients: {len(targets)}\nReply YES to send or NO to cancel."}


def _finalize_confirmed_order(arguments: Dict[str, Any], user: Dict[str, str], style: str) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    if not arguments.get("address"):
        return "❌ Cannot place order without delivery address."
    if not arguments.get("address"):
        return "❌ Delivery address missing. Please send your address to continue."
    order = create_order(business_id, user["phone"], arguments.get("items", []), arguments.get("address", ""))
    print("ORDER CREATED:", order.model_dump())
    vendor_notified = send_vendor_order_notification(order, business_id=business_id)
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    if vendor_notified:
        return f"Order placed successfully. Order ID: {order.orderId}. Total: Rs {order.total:.2f}."
    return f"Your order is placed successfully. Order ID: {order.orderId}. Vendor will be notified shortly."


def _finalize_confirmed_add_product(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    result = add_product(business_id=business_id, name=arguments.get("name", ""), price=float(arguments.get("price")), unit=arguments.get("unit") or "unit", category=arguments.get("category"), description=arguments.get("description", ""), tags=arguments.get("tags") or [], attributes=arguments.get("attributes") or {})
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    return f"Product added: {result['display_name']} at Rs {result['price']:.2f} per {result['unit']}."


def _finalize_confirmed_update_product(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    result = update_product(business_id=business_id, name=arguments.get("name", ""), price=arguments.get("price"), unit=arguments.get("unit"), category=arguments.get("category"), description=arguments.get("description"), tags=arguments.get("tags"), attributes=arguments.get("attributes"), active=arguments.get("active"))
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    if not result:
        return "I couldn’t update that product right now."
    return f"Product updated: {result['display_name']} is now Rs {result['price']:.2f} per {result['unit']}."


def _finalize_confirmed_delete_product(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    deleted = delete_product(arguments.get("name", ""), business_id=business_id)
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    if not deleted:
        return "I couldn’t remove that product right now."
    return f"{str(arguments.get('name', '')).title()} has been removed from the active catalog."


def _finalize_confirmed_offer(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    result = update_product_offer(arguments.get("product", ""), float(arguments.get("price")), arguments.get("date"), business_id=business_id)
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    return f"Offer created for {result['display_name']} at Rs {result['offer_price']:.2f}."


def _finalize_confirmed_promotion(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    result = create_promotion(business_id=business_id, title=arguments.get("title", ""), scope=arguments.get("scope") or "global", target=arguments.get("target"), discount_type=arguments.get("discount_type") or "percent", discount_value=float(arguments.get("discount_value")), starts_at=arguments.get("starts_at"), ends_at=arguments.get("ends_at"), broadcast_text=arguments.get("broadcast_text"))
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    if result.get("broadcast_text"):
        broadcast_result = execute_broadcast(result["broadcast_text"], get_customer_broadcast_targets(business_id), from_number=get_business_profile(business_id).get("whatsapp_number", ""))
        return f"Promotion created: {result['title']}.\nBroadcast sent to {broadcast_result['sent']} recipients."
    return f"Promotion created: {result['title']}."


def _finalize_confirmed_event(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    result = create_event(business_id=business_id, title=arguments.get("title", ""), description=arguments.get("description", ""), starts_at=arguments.get("starts_at"), ends_at=arguments.get("ends_at"), promotion_scope=arguments.get("promotion_scope"), promotion_target=arguments.get("promotion_target"), discount_type=arguments.get("discount_type"), discount_value=arguments.get("discount_value"), broadcast_text=arguments.get("broadcast_text"))
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    if result.get("broadcast_text"):
        broadcast_result = execute_broadcast(result["broadcast_text"], get_customer_broadcast_targets(business_id), from_number=get_business_profile(business_id).get("whatsapp_number", ""))
        return f"Event created: {result['title']}.\nBroadcast sent to {broadcast_result['sent']} recipients."
    return f"Event created: {result['title']}."


def _finalize_confirmed_business_info(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    result = update_business_profile(business_id=business_id, name=arguments.get("name"), business_type=arguments.get("business_type"), description=arguments.get("description"), address=arguments.get("address"), hours=arguments.get("hours"), contact_phone=arguments.get("contact_phone"), support_details=arguments.get("support_details"), policies=arguments.get("policies"), delivery_info=arguments.get("delivery_info"), broadcast_groups=arguments.get("broadcast_groups"))
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    return f"Business profile updated for {result['name']}."


def _finalize_confirmed_broadcast(arguments: Dict[str, Any], user: Dict[str, str]) -> str:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    result = execute_broadcast(arguments.get("message", ""), get_customer_broadcast_targets(business_id), from_number=get_business_profile(business_id).get("whatsapp_number", ""))
    clear_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID)
    return f"Broadcast sent to {result['sent']} recipients."


def save_pending_draft(user: Dict[str, str], tool_name: str, style: str, last_message: str, partial_data: Dict[str, Any]) -> None:
    set_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID, role=user["role"], action_type="draft_tool", data={"tool": tool_name, "data": partial_data}, last_language=style, last_message=last_message)



def _handle_order_draft_progress(message: str, user: Dict[str, str], style: str, state: Dict[str, Any]) -> Optional[str]:
    business_id = user.get("business_id") or DEFAULT_BUSINESS_ID
    draft = _get_draft_data(state)
    items = list(draft.get("items", []))
    address = str(draft.get("address") or "").strip()
    stripped = message.strip()

    if not items:
        clear_pending_action(user["phone"], business_id=business_id)
        return None

    if is_address(stripped):
        address = stripped

        # 🔥 NEW: persist address immediately
        latest_order = get_latest_order(user["phone"], business_id=business_id)
        if latest_order and not latest_order.address:
            update_order_address(latest_order.orderId, address, business_id)

        _queue_confirmation(
            user,
            style,
            "create_order",
            {"items": items, "address": address},
            address
        )

        return build_order_preview(items, address, style=style, business_id=business_id)["message"]

    extracted = _extract_basic_items(stripped, business_id=business_id)
    if extracted:
        merged = apply_cart_operation(items, extracted, "add")
        save_pending_draft(user, "create_order", style, _summarize_items(merged), {"items": merged, "address": address})
        return _draft_message(merged, style, operation="add")

    if style == "manglish":
        return "Order draft save cheythittundu. Iniyum items add cheyyam, allenkil delivery address ayakkam."
    if style == "malayalam":
        return "Order draft save ചെയ്തിട്ടുണ്ട്. കൂടുതൽ items add ചെയ്യാം, അല്ലെങ്കിൽ delivery address അയക്കാം."
    return "Your order draft is saved. You can add more items or send your delivery address."


def _get_draft_data(state: Dict[str, Any]) -> Dict[str, Any]:
    pending = state.get("pendingAction") or {}
    if pending.get("type") == "draft_tool" and pending.get("data", {}).get("tool") == "create_order":
        return dict(pending.get("data", {}).get("data", {}))
    return {"items": [], "address": ""}


def merge_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, int] = {}
    for item in items:
        name = normalize_product_name(str(item.get("name", "")).strip())
        qty = max(1, _safe_int(item.get("qty", 1), default=1))
        if name:
            merged[name] = merged.get(name, 0) + qty
    return [{"name": name, "qty": qty} for name, qty in merged.items()]


def apply_cart_operation(existing_items: List[Dict[str, Any]], incoming_items: List[Dict[str, Any]], operation: str) -> List[Dict[str, Any]]:
    existing_map: Dict[str, int] = {}
    for item in existing_items:
        name = normalize_product_name(str(item.get("name", "")).strip())
        qty = max(1, _safe_int(item.get("qty", 1), default=1))
        if name:
            existing_map[name] = qty

    if operation == "replace":
        return merge_items(incoming_items)

    if operation == "remove":
        for item in incoming_items:
            name = normalize_product_name(str(item.get("name", "")).strip())
            qty = max(1, _safe_int(item.get("qty", 1), default=1))
            if name in existing_map:
                remaining = existing_map[name] - qty
                if remaining > 0:
                    existing_map[name] = remaining
                else:
                    existing_map.pop(name, None)
        return [{"name": name, "qty": qty} for name, qty in existing_map.items()]

    return merge_items(existing_items + incoming_items)


def product_exists(name: str, business_id: str = DEFAULT_BUSINESS_ID) -> bool:
    return normalize_product_name(name) in get_product_catalog_map(business_id=business_id)


def infer_category_from_message(message: str) -> Optional[str]:
    lowered = message.lower()
    if "drink" in lowered or "cold" in lowered:
        return "drink"
    if "snack" in lowered or "chips" in lowered:
        return "snack"
    if "shoe" in lowered:
        return "footwear"
    if any(token in lowered for token in ["shirt", "dress", "pant", "clothing"]):
        return "clothing"
    return None


def is_address(text: str) -> bool:
    if len(text.split()) < 3:
        return False
    lowered = text.lower()
    if any(word in lowered for word in ["want", "need", "add", "order", "product", "price", "available"]):
        return False
    if any(char.isdigit() for char in text) or any(token in lowered for token in ["road", "street", "lane", "house", "flat", "near", "pin", "city", "district"]):
        return True
    return True


def _summarize_items(items: List[Dict[str, Any]]) -> str:
    return ", ".join(f"{item.get('name', '')} x {item.get('qty', 1)}" for item in items)


def _draft_message(items: List[Dict[str, Any]], style: str, operation: str = "add") -> str:
    summary = _summarize_items(items)
    if operation == "remove":
        lead_en = "I updated your order."
        lead_mg = "Order update cheythu."
        lead_ml = "Order update ചെയ്തു."
    elif operation == "replace":
        lead_en = "I replaced your order items."
        lead_mg = "Order items replace cheythu."
        lead_ml = "Order items replace ചെയ്തു."
    else:
        lead_en = "So far I have:"
        lead_mg = "Ippo order-il:"
        lead_ml = "ഇപ്പോൾ order-ിൽ:"
    if style == "manglish":
        return f"{lead_mg} {summary}\nIniyum items add cheyyam, remove cheyyam, allenkil ready aayal address ayakkam."
    if style == "malayalam":
        return f"{lead_ml} {summary}\nഇനിയും items add ചെയ്യാം, remove ചെയ്യാം, അല്ലെങ്കിൽ ready ആണെങ്കിൽ address അയക്കാം."
    return f"{lead_en} {summary}\nYou can add more items, remove items, or when you’re ready just send your delivery address."


def _localized_cancel(style: str) -> str:
    if style == "manglish":
        return "Cancel cheythu. Oru maattavum cheythilla."
    if style == "malayalam":
        return "Cancel ചെയ്തു. മാറ്റങ്ങളൊന്നും വരുത്തിയിട്ടില്ല."
    return "Cancelled. No changes were made."


def _localized_missing_order(style: str) -> str:
    if style == "manglish":
        return "Sorry, ee number-il recent order kandilla. Kooduthal details ayachal njan nokkam."
    if style == "malayalam":
        return "ക്ഷമിക്കണം, ഈ നമ്പറിൽ recent order കണ്ടില്ല. കൂടുതൽ details അയച്ചാൽ ഞാൻ നോക്കാം."
    return "Sorry, I couldn’t find a recent order for this number. If you share more details, I can help further."


def _format_product_change_preview(action: str, args: Dict[str, Any]) -> str:
    lines = [f"Product {action} preview:", f"Name: {str(args.get('name', '')).title()}"]
    if args.get("price") is not None:
        lines.append(f"Price: Rs {float(args['price']):.2f}")
    if args.get("unit"):
        lines.append(f"Unit: {args['unit']}")
    if args.get("category"):
        lines.append(f"Category: {args['category']}")
    if args.get("description"):
        lines.append(f"Description: {args['description']}")
    if args.get("tags"):
        lines.append(f"Tags: {', '.join(args['tags'])}")
    lines.append("Reply YES to confirm or NO to cancel.")
    return "\n".join(lines)


def _format_promotion_preview(args: Dict[str, Any]) -> str:
    lines = ["Promotion preview:", f"Title: {args.get('title', '')}", f"Scope: {args.get('scope', 'global')}"]
    if args.get("target"):
        lines.append(f"Target: {args['target']}")
    lines.append(f"Discount: {args.get('discount_value')} {args.get('discount_type', 'percent')}")
    if args.get("starts_at"):
        lines.append(f"Starts: {args['starts_at']}")
    if args.get("ends_at"):
        lines.append(f"Ends: {args['ends_at']}")
    if args.get("broadcast_text"):
        lines.append(f"Broadcast: {args['broadcast_text']}")
    lines.append("Reply YES to confirm or NO to cancel.")
    return "\n".join(lines)


def _format_event_preview(args: Dict[str, Any]) -> str:
    lines = ["Event preview:", f"Title: {args.get('title', '')}", f"Description: {args.get('description', '')}"]
    if args.get("starts_at"):
        lines.append(f"Starts: {args['starts_at']}")
    if args.get("ends_at"):
        lines.append(f"Ends: {args['ends_at']}")
    if args.get("discount_type") and args.get("discount_value") is not None:
        lines.append(f"Discount: {args['discount_value']} {args['discount_type']}")
    if args.get("promotion_scope"):
        lines.append(f"Discount scope: {args['promotion_scope']}")
    if args.get("promotion_target"):
        lines.append(f"Discount target: {args['promotion_target']}")
    if args.get("broadcast_text"):
        lines.append(f"Broadcast: {args['broadcast_text']}")
    lines.append("Reply YES to confirm or NO to cancel.")
    return "\n".join(lines)


def _format_business_profile_preview(args: Dict[str, Any]) -> str:
    labels = {"name": "Name", "business_type": "Business type", "description": "Description", "address": "Address", "hours": "Hours", "contact_phone": "Contact phone", "support_details": "Support details", "policies": "Policies", "delivery_info": "Delivery info", "broadcast_groups": "Broadcast groups"}
    lines = ["Business profile preview:"]
    changed = False
    for key, label in labels.items():
        value = args.get(key)
        if value is None or value == "" or value == []:
            continue
        changed = True
        lines.append(f"{label}: {', '.join(value) if isinstance(value, list) else value}")
    if not changed:
        lines.append("No valid changes were provided.")
    lines.append("Reply YES to confirm or NO to cancel.")
    return "\n".join(lines)


def _queue_confirmation(user: Dict[str, str], style: str, tool: str, arguments: Dict[str, Any], last_message: str) -> None:
    set_pending_action(user["phone"], business_id=user.get("business_id") or DEFAULT_BUSINESS_ID, role=user["role"], action_type="confirm_tool", data={"tool": tool, "arguments": arguments}, last_language=style, last_message=last_message)


def _store_turn(phone: str, user_message: str, assistant_message: str, business_id: str = DEFAULT_BUSINESS_ID) -> None:
    append_history(phone, "user", user_message, business_id=business_id)
    append_history(phone, "assistant", assistant_message, business_id=business_id)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_basic_items(message: str, business_id: str = DEFAULT_BUSINESS_ID) -> List[Dict[str, Any]]:
    words = [word.strip(",. ") for word in message.split() if word.strip(",. ")]
    items: List[Dict[str, Any]] = []
    for index, word in enumerate(words):
        if word.isdigit() and index + 1 < len(words):
            product_name = normalize_product_name(words[index + 1])
            if product_exists(product_name, business_id=business_id):
                items.append({"name": product_name, "qty": int(word)})
    return items


def _infer_vendor_status(msg: str):
    msg = msg.lower()

    if any(x in msg for x in ["accept", "ok", "yes"]):
        return "accepted"

    if any(x in msg for x in ["dispatch", "sending", "out"]):
        return "out_for_delivery"

    if any(x in msg for x in ["deliver", "done", "completed"]):
        return "delivered"

    return None


def _infer_business_topic(message: str) -> Optional[str]:
    if any(token in message for token in ["hour", "time", "open", "close"]):
        return "hours"
    if any(token in message for token in ["address", "location", "where"]):
        return "address"
    if any(token in message for token in ["deliver", "shipping"]):
        return "delivery"
    if any(token in message for token in ["policy", "return", "exchange"]):
        return "policies"
    if any(token in message for token in ["contact", "phone", "support"]):
        return "support"
    return None


def format_product_response(
    products: List[Dict[str, Any]],
    style: str,
    intro: str = "Here are some options you might like:",
    filters: Optional[Dict[str, Any]] = None,
) -> str:
    if not products:
        if style == "manglish":
            return "Ippo items available alla. Kurachu kazhinju nokkam."
        if style == "malayalam":
            return "ഇപ്പോൾ items available അല്ല. കുറച്ച് കഴിഞ്ഞ് വീണ്ടും നോക്കാം."
        return "There are no products available right now."
    filter_line = _format_product_filters(filters or {})
    lines = []
    for product in products[:5]:
        name = str(product.get("display_name") or product.get("name", "")).title()
        emoji = _product_emoji(product.get("name", ""))
        price = f"Rs {float(product.get('offer_price') or product.get('price', 0)):.2f}"
        description = str(product.get("description") or "").strip()
        line = f"{emoji} {name} - {price}"
        if description:
            line += f" | {description}"
        lines.append(line)
    if style == "manglish":
        prefix = "Ivide kurachu options undu:"
        if filter_line:
            prefix += f"\n{filter_line}"
        return prefix + "\n" + "\n".join(lines) + "\n\nEnthanu venam ennu parayu, njan add cheyyam."
    if style == "malayalam":
        prefix = "ഇവിടെ ചില options ഉണ്ട്:"
        if filter_line:
            prefix += f"\n{filter_line}"
        return prefix + "\n" + "\n".join(lines) + "\n\nഎന്താണ് വേണ്ടത് എന്ന് പറഞ്ഞാൽ ഞാൻ add ചെയ്യും."
    prefix = intro
    if filter_line:
        prefix += "\n" + filter_line
    return prefix + "\n" + "\n".join(lines) + "\n\nTell me what you'd like and I’ll add it for you."


def format_product_detail_response(product: Dict[str, Any], style: str) -> str:
    name = str(product.get("display_name") or product.get("name", "")).title()
    price = float(product.get("offer_price") or product.get("price", 0))
    unit = str(product.get("unit") or "unit")
    category = str(product.get("category") or "other")
    description = str(product.get("description") or "").strip()
    tags = ", ".join(product.get("tags", [])[:5])
    lines = [f"{_product_emoji(product.get('name', ''))} {name}", f"Price: Rs {price:.2f} per {unit}", f"Category: {category}"]
    if description:
        lines.append(f"Details: {description}")
    if tags:
        lines.append(f"Tags: {tags}")
    lines.append("If you'd like it, tell me the quantity and I’ll add it to your order." if style == "english" else ("Venel quantity paranjal njan order-il add cheyyam." if style == "manglish" else "വേണമെങ്കിൽ quantity പറഞ്ഞാൽ ഞാൻ order-ിൽ add ചെയ്യാം."))
    return "\n".join(lines)


def format_business_info_response(profile: Dict[str, Any], style: str, topic: Optional[str]) -> str:
    topic_value = (topic or "").lower()
    lines: List[str] = []
    if topic_value in {"hours", "timing"} and profile.get("hours"):
        lines.append(f"Hours: {profile['hours']}")
    elif topic_value in {"address", "location"} and profile.get("address"):
        lines.append(f"Address: {profile['address']}")
    elif topic_value in {"support", "contact"}:
        if profile.get("contact_phone"):
            lines.append(f"Contact: {profile['contact_phone']}")
        if profile.get("support_details"):
            lines.append(f"Support: {profile['support_details']}")
    elif topic_value in {"policy", "policies", "return"} and profile.get("policies"):
        lines.append(f"Policies: {profile['policies']}")
    elif topic_value in {"delivery", "shipping"} and profile.get("delivery_info"):
        lines.append(f"Delivery: {profile['delivery_info']}")
    else:
        for key, label in [("name", None), ("description", None), ("address", "Address"), ("hours", "Hours"), ("delivery_info", "Delivery"), ("policies", "Policies"), ("support_details", "Support"), ("contact_phone", "Contact")]:
            value = profile.get(key)
            if not value:
                continue
            if label is None and key == "name":
                lines.append(f"{value} ({profile.get('business_type', 'business')})")
            elif label is None:
                lines.append(str(value))
            else:
                lines.append(f"{label}: {value}")
    lines.append("Iniyum enthenkilum details venel chodikkam." if style == "manglish" else ("ഇനിയും എന്തെങ്കിലും details വേണമെങ്കിൽ ചോദിക്കാം." if style == "malayalam" else "If you want anything else, I can help with products, orders, or support."))
    return "\n".join(lines)


def _format_product_filters(filters: Dict[str, Any]) -> str:
    parts: List[str] = []
    if filters.get("filter_category"):
        parts.append(f"Category: {filters['filter_category']}")
    if filters.get("search"):
        parts.append(f"Search: {filters['search']}")
    if filters.get("min_price") is not None or filters.get("max_price") is not None:
        min_price = filters.get("min_price")
        max_price = filters.get("max_price")
        if min_price is not None and max_price is not None:
            parts.append(f"Price: Rs {float(min_price):.0f} to Rs {float(max_price):.0f}")
        elif min_price is not None:
            parts.append(f"Price: above Rs {float(min_price):.0f}")
        elif max_price is not None:
            parts.append(f"Price: below Rs {float(max_price):.0f}")
    if filters.get("tags"):
        parts.append("Tags: " + ", ".join(filters["tags"]))
    if filters.get("sort_by") == "price":
        direction = "low to high" if filters.get("sort_order", "asc") == "asc" else "high to low"
        parts.append(f"Sorted by price ({direction})")
    return " | ".join(parts)


def _product_emoji(name: str) -> str:
    category = get_product_category(name)
    normalized = name.lower()
    if "coffee" in normalized or "tea" in normalized:
        return "☕"
    if category == "drink":
        return "🥤"
    if category == "snack":
        return "🍟"
    if category == "clothing":
        return "👕"
    if category == "footwear":
        return "👟"
    return "🛍️"


TOOL_DEFINITIONS = [
    {"name": "get_products", "description": "Get available products from the MongoDB catalog. Supports optional filter_category and search."},
    {"name": "get_product_details", "description": "Get detailed information about one product from the catalog, including description, price, tags, and category."},
    {"name": "get_business_info", "description": "Get business details from the database such as address, hours, contact, delivery info, support details, and policies."},
    {"name": "create_order", "description": "Prepare or update a multi-item customer order. Supports operation add, replace, or remove. Address can be omitted until checkout."},
    {"name": "get_order_status", "description": "Fetch the latest order status for the current customer."},
    {"name": "create_support_ticket", "description": "Create a customer support ticket for complaints, wrong items, delivery issues, cancellations, or vendor contact requests."},
    {"name": "update_order_status", "description": "Update vendor order status to accepted, rejected, out_for_delivery, or delivered."},
    {"name": "add_product", "description": "Add a new product to the MongoDB product catalog with price, unit, category, description, tags, and optional attributes."},
    {"name": "update_product", "description": "Update an existing product's price, unit, category, description, tags, attributes, or active state."},
    {"name": "delete_product", "description": "Deactivate an existing product so it no longer appears in the active catalog."},
    {"name": "create_offer", "description": "Create a new offer for an existing product."},
    {"name": "create_promotion", "description": "Create a promotion that affects billing. Supports global, category, or product discounts with optional broadcast text."},
    {"name": "create_event", "description": "Create a business event with optional discount metadata and optional broadcast text."},
    {"name": "update_business_info", "description": "Update business profile details such as store name, type, description, address, hours, contact phone, support details, policies, delivery info, and broadcast groups."},
    {"name": "send_broadcast", "description": "Send a marketing broadcast to known customers and configured WhatsApp groups."},
    {
    "name": "resolve_support_ticket",
    "description": "Resolve a support ticket with a resolution message and update its status to resolved."
    },
]





