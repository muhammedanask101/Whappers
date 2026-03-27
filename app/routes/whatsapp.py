import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Response, status

from app.services.broadcast import create_broadcast_preview, execute_broadcast
from app.services.llm import (
    classify_intent,
    classify_vendor_intent,
    detect_language_style,
    extract_address,
    extract_items,
    extract_vendor_action,
    generate_support_reply,
    is_confirmation_no,
    is_confirmation_yes,
    localize_message,
)
from app.services.order import (
    build_order_preview,
    create_order,
    format_order_summary,
    get_latest_order,
    get_latest_vendor_actionable_order,
    update_order_status,
)
from app.services.state import clear_pending_action, get_conversation_state, set_pending_action, update_last_language
from app.services.vendor import (
    format_catalog,
    format_offer_preview,
    format_price_update_preview,
    generate_vendor_acknowledgement,
    get_customer_broadcast_targets,
    match_products,
    normalize_phone,
    notify_customer_order_update,
    send_vendor_order_notification,
    send_whatsapp_message,
    update_product_offer,
    update_product_price,
)

router = APIRouter(tags=["whatsapp"])


def _vendor_phone() -> str:
    value = os.getenv("VENDOR_PHONE", "").strip()
    if not value:
        raise RuntimeError("VENDOR_PHONE is not configured.")
    return normalize_phone(value)


def _get_sender_role(sender: str) -> str:
    return "vendor" if normalize_phone(sender) == _vendor_phone() else "customer"


def _build_cancel_preview(order: Any, style: str) -> str:
    return localize_message(
        "cancel_preview",
        style,
        order_id=order.orderId,
        items=format_order_summary(order.items),
    )


def _handle_confirmation_reply(sender: str, body: str, state: Dict[str, Any]) -> Optional[str]:
    pending = state.get("pendingAction")
    if not pending:
        return None

    style = state.get("lastLanguage") or detect_language_style(body)
    if is_confirmation_no(body):
        clear_pending_action(sender)
        return localize_message("action_cancelled", style)

    if not is_confirmation_yes(body):
        return None

    action_type = pending["type"]
    data = pending["data"]

    if action_type == "confirm_order":
        order = create_order(customer_phone=sender, items=data["items"], address=data["address"])
        clear_pending_action(sender)
        send_vendor_order_notification(order)
        return localize_message("order_created", style, order_id=order.orderId, total=f"{order.total:.2f}")

    if action_type == "cancel_order":
        updated = update_order_status(data["orderId"], "rejected")
        clear_pending_action(sender)
        if updated:
            send_whatsapp_message(_vendor_phone(), f"Customer cancelled order {updated.orderId}. Status set to rejected.")
        return localize_message("order_cancelled", style, order_id=data["orderId"])

    if action_type == "vendor_order_action":
        updated = update_order_status(data["orderId"], data["status"])
        clear_pending_action(sender)
        if not updated:
            return localize_message("generic_error", style)
        notify_customer_order_update(updated.customerPhone, updated, style_hint=data.get("customerStyle", "english"))
        return generate_vendor_acknowledgement(updated, style, data["status"])

    if action_type == "vendor_create_offer":
        preview = update_product_offer(
            product=data["product"],
            offer_price=float(data["price"]),
            offer_until=data.get("date"),
        )
        clear_pending_action(sender)
        return localize_message(
            "offer_created",
            style,
            product=preview["product"],
            price=f"{preview['offer_price']:.2f}",
            date=preview.get("offer_until") or "open-ended",
        )

    if action_type == "vendor_update_price":
        preview = update_product_price(product=data["product"], price=float(data["price"]))
        clear_pending_action(sender)
        return localize_message(
            "price_updated",
            style,
            product=preview["product"],
            price=f"{preview['price']:.2f}",
        )

    if action_type == "vendor_broadcast":
        result = execute_broadcast(data["message"], data["targets"])
        clear_pending_action(sender)
        return localize_message("broadcast_sent", style, count=str(result["sent"]))

    return None


def _prepare_customer_order(sender: str, body: str, style: str) -> str:
    items = extract_items(body)
    if not items:
        return localize_message("ask_items", style)

    address = extract_address(body)
    if not address:
        set_pending_action(
            sender,
            role="customer",
            action_type="collect_address",
            data={"items": items},
            last_language=style,
            last_message=body,
        )
        return localize_message("ask_address", style)

    preview = build_order_preview(items=items, address=address, style=style)
    set_pending_action(
        sender,
        role="customer",
        action_type="confirm_order",
        data={"items": items, "address": address},
        last_language=style,
        last_message=body,
    )
    return preview["message"]


def _handle_customer_message(sender: str, body: str) -> str:
    style = detect_language_style(body)
    update_last_language(sender, "customer", style, body)
    state = get_conversation_state(sender)

    confirmation_response = _handle_confirmation_reply(sender, body, state)
    if confirmation_response:
        return confirmation_response

    pending = state.get("pendingAction")
    if pending and pending["type"] == "collect_address":
        if is_confirmation_no(body):
            clear_pending_action(sender)
            return localize_message("action_cancelled", style)
        if is_confirmation_yes(body):
            return localize_message("ask_address", style)
        address = body.strip()
        preview = build_order_preview(items=pending["data"]["items"], address=address, style=style)
        set_pending_action(
            sender,
            role="customer",
            action_type="confirm_order",
            data={"items": pending["data"]["items"], "address": address},
            last_language=style,
            last_message=body,
        )
        return preview["message"]

    intent = classify_intent(body)

    if intent == "browse_products":
        matches = match_products(body)
        return format_catalog(style=style, products=matches)

    if intent == "place_order":
        return _prepare_customer_order(sender, body, style)

    if intent == "confirm_order":
        if pending:
            return localize_message("awaiting_yes_no", style)
        return _prepare_customer_order(sender, body, style)

    if intent == "order_status":
        order = get_latest_order(sender)
        if not order:
            return localize_message("no_orders", style)
        return localize_message(
            "order_status",
            style,
            order_id=order.orderId,
            status=order.status.replace("_", " "),
            total=f"{order.total:.2f}",
        )

    if intent == "cancel_order":
        order = get_latest_order(sender)
        if not order:
            return localize_message("no_orders", style)
        if order.status in {"delivered", "rejected"}:
            return localize_message("cannot_cancel", style, status=order.status.replace("_", " "))
        set_pending_action(
            sender,
            role="customer",
            action_type="cancel_order",
            data={"orderId": order.orderId},
            last_language=style,
            last_message=body,
        )
        return _build_cancel_preview(order, style)

    return generate_support_reply(body, style)


def _resolve_vendor_order_action(sender: str, action: Dict[str, Any], style: str) -> str:
    intent = action["intent"]
    status_map = {
        "accept_order": "accepted",
        "reject_order": "rejected",
        "out_for_delivery": "out_for_delivery",
        "delivered": "delivered",
    }
    target_status = status_map[intent]
    order = get_latest_vendor_actionable_order(target_order_id=action.get("orderId"), desired_status=target_status)
    if not order:
        return localize_message("vendor_no_target_order", style)

    customer_state = get_conversation_state(order.customerPhone)
    set_pending_action(
        sender,
        role="vendor",
        action_type="vendor_order_action",
        data={
            "orderId": order.orderId,
            "status": target_status,
            "customerStyle": customer_state.get("lastLanguage", "english"),
        },
        last_language=style,
        last_message=action.get("rawMessage", ""),
    )
    return localize_message(
        "vendor_order_preview",
        style,
        order_id=order.orderId,
        next_status=target_status.replace("_", " "),
        customer=order.customerPhone,
        items=format_order_summary(order.items),
    )


def _handle_vendor_message(sender: str, body: str) -> str:
    style = detect_language_style(body)
    update_last_language(sender, "vendor", style, body)
    state = get_conversation_state(sender)

    confirmation_response = _handle_confirmation_reply(sender, body, state)
    if confirmation_response:
        return confirmation_response

    intent = classify_vendor_intent(body)
    action = extract_vendor_action(body, intent=intent)
    action["rawMessage"] = body

    if intent in {"accept_order", "reject_order", "out_for_delivery", "delivered"}:
        return _resolve_vendor_order_action(sender, action, style)

    if intent == "create_offer":
        if not action.get("product") or action.get("price") is None:
            return localize_message("vendor_offer_missing_fields", style)
        set_pending_action(
            sender,
            role="vendor",
            action_type="vendor_create_offer",
            data=action,
            last_language=style,
            last_message=body,
        )
        return format_offer_preview(action, style)

    if intent == "update_price":
        if not action.get("product") or action.get("price") is None:
            return localize_message("vendor_price_missing_fields", style)
        set_pending_action(
            sender,
            role="vendor",
            action_type="vendor_update_price",
            data=action,
            last_language=style,
            last_message=body,
        )
        return format_price_update_preview(action, style)

    if intent == "broadcast_message":
        targets = get_customer_broadcast_targets()
        preview = create_broadcast_preview(body, targets, style)
        set_pending_action(
            sender,
            role="vendor",
            action_type="vendor_broadcast",
            data={"message": preview["message"], "targets": targets},
            last_language=style,
            last_message=body,
        )
        return preview["preview"]

    return localize_message("vendor_fallback", style)


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(Body: str = Form(""), From: str = Form("")) -> Response:
    if not Body or not From:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Twilio WhatsApp webhook fields.",
        )

    sender = normalize_phone(From)
    role = _get_sender_role(sender)
    reply = _handle_vendor_message(sender, Body) if role == "vendor" else _handle_customer_message(sender, Body)
    send_whatsapp_message(sender, reply)
    return Response(content="", media_type="text/plain", status_code=status.HTTP_200_OK)
