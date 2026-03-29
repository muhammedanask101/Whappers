from __future__ import annotations

from collections import Counter
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now
from app.services.agent.learning import (
    get_tool_policy_stats,
    record_event,
    record_recommendation_feedback,
    record_tool_outcome,
    update_learning_profile,
)
from app.services.agent.localization import (
    ask_address_text,
    format_cart_message,
    order_placed_text,
    order_status_text,
    recommendation_intro,
    status_phrase,
    style_text,
    support_ticket_text,
)
from app.services.agent.ranking import rank_products
from app.services.agent.semantic_memory import retrieve, summarize_for_prompt
from app.services.agent.types import ToolObservation
from app.services.broadcast import execute_broadcast
from app.services.cart_engine import apply_cart_action, parse_cart_requests
from app.services.intelligence.recommendations import recommend_products
from app.services.llm import call_agent_llm
from app.services.order import (
    build_order_preview,
    create_order,
    get_latest_order,
    get_latest_vendor_actionable_order,
    get_order_by_id,
    update_order_status,
)
from app.services.vendor import (
    add_product,
    create_event,
    create_promotion,
    create_support_ticket,
    delete_product,
    generate_vendor_acknowledgement,
    get_active_events,
    get_active_promotions,
    get_business_profile,
    get_customer_broadcast_targets,
    get_product_catalog,
    get_product_details,
    normalize_product_name,
    notify_customer_order_update,
    send_vendor_order_notification,
    update_business_profile,
    update_product,
    update_product_offer,
)


ToolHandler = Callable[[Dict[str, Any], Dict[str, Any], Dict[str, Any], str], ToolObservation]
logger = logging.getLogger(__name__)


TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "inspect_customer_context": {"description": "Inspect customer profile, history, and commercial context.", "roles": {"customer", "vendor"}, "domains": {"sales", "order", "support"}, "internal": True, "base_score": 0.75},
    "inspect_order_context": {"description": "Inspect current cart, recent orders, and workflow readiness.", "roles": {"customer", "vendor"}, "domains": {"order", "support"}, "internal": True, "base_score": 0.78},
    "inspect_support_context": {"description": "Inspect open tickets, service history, and recovery context.", "roles": {"customer", "vendor"}, "domains": {"support"}, "internal": True, "base_score": 0.82},
    "inspect_business_context": {"description": "Inspect business profile, policies, promotions, and delivery context.", "roles": {"customer", "vendor"}, "domains": {"sales", "order", "support"}, "internal": True, "base_score": 0.55},
    "inspect_memory_context": {"description": "Retrieve relevant semantic memories, prior preferences, and past resolutions.", "roles": {"customer", "vendor"}, "domains": {"sales", "order", "support"}, "internal": True, "base_score": 0.7},
    "repair_order_context": {"description": "Repair an active order flow after a correction, especially wrong address capture or slot confusion.", "roles": {"customer"}, "domains": {"order", "support"}, "internal": False, "base_score": 0.88},
    "search_catalog": {"description": "Semantic product search with personalization and ranking.", "roles": {"customer", "vendor"}, "domains": {"sales", "order"}, "internal": False, "base_score": 0.8},
    "get_product_details": {"description": "Get rich details for one product.", "roles": {"customer", "vendor"}, "domains": {"sales", "order"}, "internal": False, "base_score": 0.72},
    "manage_cart": {"description": "Add, remove, replace, or view cart items using semantic and learned matching.", "roles": {"customer"}, "domains": {"order", "sales"}, "internal": False, "base_score": 0.92},
    "collect_address": {"description": "Capture, verify, or update the delivery address.", "roles": {"customer"}, "domains": {"order"}, "internal": False, "base_score": 0.9},
    "checkout_order": {"description": "Finalize the current draft cart into an order.", "roles": {"customer"}, "domains": {"order"}, "internal": False, "base_score": 0.96},
    "get_order_status": {"description": "Get the latest or referenced order status.", "roles": {"customer", "vendor"}, "domains": {"order", "support"}, "internal": False, "base_score": 0.9},
    "create_support_ticket": {"description": "Create or update a support ticket for an issue.", "roles": {"customer"}, "domains": {"support"}, "internal": False, "base_score": 0.95},
    "recommend_products": {"description": "Recommend next-best products using personalization, exploration, and affinity.", "roles": {"customer"}, "domains": {"sales"}, "internal": False, "base_score": 0.78},
    "vendor_update_order_status": {"description": "Update vendor order status from natural language intent.", "roles": {"vendor"}, "domains": {"order", "support"}, "internal": False, "base_score": 0.96},
    "resolve_support_ticket": {"description": "Resolve an existing support ticket and notify the customer.", "roles": {"vendor"}, "domains": {"support"}, "internal": False, "base_score": 0.92},
    "manage_product": {"description": "Add, update, or deactivate a product.", "roles": {"vendor"}, "domains": {"sales"}, "internal": False, "base_score": 0.84},
    "create_offer": {"description": "Set an offer price for a product.", "roles": {"vendor"}, "domains": {"sales"}, "internal": False, "base_score": 0.78},
    "create_promotion": {"description": "Create a promotion for discounts.", "roles": {"vendor"}, "domains": {"sales"}, "internal": False, "base_score": 0.74},
    "create_event": {"description": "Create a business event with optional discount metadata.", "roles": {"vendor"}, "domains": {"sales"}, "internal": False, "base_score": 0.68},
    "update_business_profile": {"description": "Update hours, address, support info, policies, and delivery details.", "roles": {"vendor"}, "domains": {"sales", "support", "order"}, "internal": False, "base_score": 0.66},
    "send_broadcast": {"description": "Send a targeted broadcast to customers and groups.", "roles": {"vendor"}, "domains": {"sales"}, "internal": False, "base_score": 0.6},
    "respond": {"description": "Reply directly when no tool is required.", "roles": {"customer", "vendor"}, "domains": {"sales", "order", "support"}, "internal": False, "base_score": 0.2},
}


def available_tools(user_role: str) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for name, meta in TOOL_REGISTRY.items():
        if user_role in meta["roles"]:
            tools.append(
                {
                    "name": name,
                    "description": meta["description"],
                    "internal": meta["internal"],
                    "domains": sorted(meta["domains"]),
                }
            )
    return tools


def rank_tools(
    *,
    user: Dict[str, Any],
    state: Dict[str, Any],
    analysis: Dict[str, Any],
    strategy: Dict[str, Any],
    learning_profile: Dict[str, Any],
    scratchpad: Optional[List[ToolObservation]] = None,
) -> List[Dict[str, Any]]:
    role = user.get("role", "customer")
    domain = strategy.get("mode", analysis.get("category", "sales"))
    recent_failures = {row.tool for row in (scratchpad or []) if not row.ok}
    tool_success = learning_profile.get("tool_success", {})
    global_tool_stats = get_tool_policy_stats(user.get("business_id", DEFAULT_BUSINESS_ID))
    ranked: List[Dict[str, Any]] = []

    for name, meta in TOOL_REGISTRY.items():
        if role not in meta["roles"]:
            continue
        score = float(meta.get("base_score", 0.5))
        if domain in meta["domains"]:
            score += 0.24
        if meta["internal"] and not scratchpad:
            score += 0.08
        if name in recent_failures:
            score -= 0.35
        success_info = tool_success.get(name, {})
        attempts = float(success_info.get("attempts", 0))
        successes = float(success_info.get("successes", 0))
        if attempts:
            score += (successes / attempts) * 0.12
        policy = global_tool_stats.get(name, {})
        policy_attempts = float(policy.get("attempts", 0.0))
        if policy_attempts:
            score += (float(policy.get("successes", 0.0)) / policy_attempts) * 0.08
            score += max(-0.12, min(0.12, float(policy.get("reward_total", 0.0)) / max(policy_attempts, 1.0) / 10.0))
        if analysis.get("intent") == "support" and name in {"inspect_support_context", "create_support_ticket", "resolve_support_ticket"}:
            score += 0.24
        if analysis.get("intent") == "cart" and name in {"inspect_order_context", "manage_cart"}:
            score += 0.26
        if analysis.get("cart_view_request") and name == "manage_cart":
            score += 0.42
        if analysis.get("intent") == "checkout" and name in {"inspect_order_context", "collect_address", "checkout_order", "repair_order_context"}:
            score += 0.26
        if analysis.get("is_correction") and name in {"repair_order_context", "inspect_order_context", "collect_address"}:
            score += 0.3
        if name == "repair_order_context" and not analysis.get("is_correction"):
            score -= 0.5
        if name == "repair_order_context" and state.get("workflow", {}).get("name") != "order_checkout":
            score -= 0.3
        if analysis.get("intent") == "order_status" and name in {"inspect_order_context", "get_order_status"}:
            score += 0.26
        if analysis.get("intent") == "browse" and name in {"inspect_customer_context", "search_catalog", "recommend_products"}:
            score += 0.18
        if (state.get("cart") or {}).get("items"):
            if name in {"inspect_order_context", "manage_cart", "collect_address", "checkout_order"}:
                score += 0.24
            if name == "search_catalog" and not analysis.get("intent") == "browse":
                score -= 0.3
        if analysis.get("order_workflow_locked"):
            if name in {"manage_cart", "inspect_order_context", "collect_address", "checkout_order", "repair_order_context"}:
                score += 0.34
            elif not meta["internal"]:
                score -= 0.45
        if analysis.get("uncertainty", 0.0) >= 0.38 and name in {"inspect_memory_context", "inspect_customer_context", "inspect_business_context"}:
            score += 0.18
        if state.get("open_ticket_id") and name in {"inspect_support_context", "resolve_support_ticket"}:
            score += 0.16
        if state.get("workflow", {}).get("stage") == "collect_address" and name == "collect_address":
            score += 0.3
        ranked.append({"name": name, "description": meta["description"], "internal": meta["internal"], "score": round(score, 4)})

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def is_useful_tool_observation(observation: ToolObservation) -> bool:
    if not observation.ok:
        return False
    if observation.internal:
        return True
    message = str(observation.message or "").strip().lower()
    if not message and not observation.data:
        return False
    generic_phrases = {
        "tell me a little more and i will handle it.",
        "tell me what you need help with, and i will handle it.",
        "tell me what should be fixed in the order.",
        "i couldn't find an exact match. tell me a category or product type and i will narrow it down.",
        "i don't have a strong recommendation right now.",
        "i couldn't find matching products right now.",
    }
    if message in generic_phrases:
        return False
    if observation.tool == "search_catalog" and not observation.data.get("products"):
        return False
    if observation.tool == "recommend_products" and not observation.data.get("products"):
        return False
    if observation.tool == "manage_cart" and not observation.data.get("items") and "empty" not in message:
        return False
    return True


def execute_tool(
    action: str,
    args: Dict[str, Any],
    state: Dict[str, Any],
    user: Dict[str, Any],
    style: str,
    memory: Optional[Dict[str, Any]] = None,
) -> ToolObservation:
    handlers: Dict[str, ToolHandler] = {
        "inspect_customer_context": _tool_inspect_customer_context,
        "inspect_order_context": _tool_inspect_order_context,
        "inspect_support_context": _tool_inspect_support_context,
        "inspect_business_context": _tool_inspect_business_context,
        "inspect_memory_context": _tool_inspect_memory_context,
        "repair_order_context": _tool_repair_order_context,
        "search_catalog": _tool_search_catalog,
        "get_product_details": _tool_get_product_details,
        "manage_cart": _tool_manage_cart,
        "collect_address": _tool_collect_address,
        "checkout_order": _tool_checkout_order,
        "get_order_status": _tool_get_order_status,
        "create_support_ticket": _tool_create_support_ticket,
        "recommend_products": _tool_recommend_products,
        "vendor_update_order_status": _tool_vendor_update_order_status,
        "resolve_support_ticket": _tool_resolve_support_ticket,
        "manage_product": _tool_manage_product,
        "create_offer": _tool_create_offer,
        "create_promotion": _tool_create_promotion,
        "create_event": _tool_create_event,
        "update_business_profile": _tool_update_business_profile,
        "send_broadcast": _tool_send_broadcast,
    }
    handler = handlers.get(action)
    if handler is None:
        return ToolObservation(tool=action, ok=False, message="", error=f"Unknown tool: {action}")
    observation = handler(args, state, user, style)
    if action != "respond":
        update_learning_profile(
            user["phone"],
            user.get("business_id", DEFAULT_BUSINESS_ID),
            message=str(args.get("raw_message") or args.get("query") or args.get("address") or ""),
            inferred_intent=action,
            tool_name=action,
            tool_ok=observation.ok,
            products=observation.data.get("products", []),
            vendor_status=observation.data.get("status", ""),
        )
        reward = 1.0 if observation.ok else -0.7
        if observation.data.get("conversion_signal"):
            reward += 1.2
        if observation.data.get("recovery_signal"):
            reward += 0.8
        record_tool_outcome(user.get("business_id", DEFAULT_BUSINESS_ID), tool_name=action, ok=observation.ok, reward=reward)
    return observation


def _tool_inspect_customer_context(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    profile = args.get("profile") or {}
    learning = args.get("learning") or {}
    memory = args.get("memory") or {}
    recent_orders = list(
        get_collection("orders")
        .find({"business_id": user.get("business_id", DEFAULT_BUSINESS_ID), "customer_phone": user["phone"]})
        .sort("created_at", -1)
        .limit(3)
    )
    data = {
        "segment": profile.get("segment", "unknown"),
        "rfm": profile.get("rfm", {}),
        "favorite_products": profile.get("favorite_products", [])[:5],
        "support_risk": profile.get("support_risk", 0.0),
        "preference_terms": learning.get("preference_terms", [])[:8],
        "recent_intents": profile.get("recent_intents", [])[-5:],
        "recent_orders": [
            {"order_id": row.get("order_id"), "status": row.get("status"), "total": float(row.get("total", 0.0))}
            for row in recent_orders
        ],
        "history_tail": list(memory.get("history", []))[-4:],
    }
    return ToolObservation(
        tool="inspect_customer_context",
        ok=True,
        message="",
        data=data,
        internal=True,
        state_patch={"customer_summary": f"segment={data['segment']}; support_risk={data['support_risk']}"},
    )


def _tool_inspect_order_context(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    latest_order = get_latest_order(user["phone"], business_id) if user.get("role") != "vendor" else None
    vendor_order = (
        get_latest_vendor_actionable_order(
            str(args.get("order_id") or "").strip() or None,
            str(args.get("status") or "").strip() or None,
            business_id=business_id,
        )
        if user.get("role") == "vendor"
        else None
    )
    data = {
        "workflow": state.get("workflow", {}),
        "cart": state.get("cart", {}),
        "latest_order": {"order_id": latest_order.orderId, "status": latest_order.status, "total": latest_order.total} if latest_order else None,
        "vendor_actionable_order": {"order_id": vendor_order.orderId, "status": vendor_order.status, "total": vendor_order.total} if vendor_order else None,
    }
    return ToolObservation(tool="inspect_order_context", ok=True, message="", data=data, internal=True)


def _tool_inspect_support_context(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    phone = user["phone"] if user.get("role") != "vendor" else str(args.get("customer_phone") or "")
    query: Dict[str, Any] = {"business_id": business_id}
    if state.get("open_ticket_id"):
        query["ticket_id"] = state["open_ticket_id"]
    elif phone:
        query["customer_phone"] = phone
    tickets = list(get_collection("support_tickets").find(query).sort("created_at", -1).limit(3))
    latest_order = get_latest_order(phone, business_id) if phone else None
    active_ticket_id = str(tickets[0].get("ticket_id") or "") if tickets else state.get("open_ticket_id", "")
    data = {
        "open_tickets": [
            {
                "ticket_id": row.get("ticket_id"),
                "status": row.get("status"),
                "issue": row.get("issue"),
                "priority": row.get("priority"),
            }
            for row in tickets
        ],
        "latest_order": {"order_id": latest_order.orderId, "status": latest_order.status, "total": latest_order.total} if latest_order else None,
        "active_ticket_id": active_ticket_id,
    }
    return ToolObservation(
        tool="inspect_support_context",
        ok=True,
        message="",
        data=data,
        internal=True,
        state_patch={"open_ticket_id": active_ticket_id},
    )


def _tool_inspect_business_context(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    profile = get_business_profile(business_id)
    data = {
        "profile": {
            "name": profile.get("name"),
            "description": profile.get("description"),
            "address": profile.get("address"),
            "hours": profile.get("hours"),
            "delivery_info": profile.get("delivery_info"),
            "support_details": profile.get("support_details"),
            "policies": profile.get("policies"),
        },
        "active_promotions": get_active_promotions(business_id)[:4],
        "active_events": get_active_events(business_id)[:4],
    }
    return ToolObservation(tool="inspect_business_context", ok=True, message="", data=data, internal=True)


def _tool_inspect_memory_context(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    query = str(args.get("query") or args.get("raw_message") or args.get("topic") or "").strip()
    rows = retrieve(user["phone"], user.get("business_id", DEFAULT_BUSINESS_ID), query=query, limit=5)
    return ToolObservation(tool="inspect_memory_context", ok=True, message="", data={"memories": summarize_for_prompt(rows)}, internal=True)


def _tool_search_catalog_legacy_removed(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    query = str(args.get("query") or args.get("search") or "").strip()
    products = get_product_catalog(
        business_id=business_id,
        filter_category=args.get("category"),
        search=query or None,
        limit=min(max(int(args.get("limit", 8) or 8), 1), 12),
    )
    ranked = rank_products(query or "products", products, profile=args.get("profile") or {}, learning_profile=args.get("learning") or {})
    top = ranked[:5]
    if not top:
        return ToolObservation(tool="search_catalog", ok=True, message=style_text(style, "I couldn't find matching products right now.", "Ippo exact match kandethan pattiyilla.", "ഇപ്പോൾ കൃത്യമായ പൊരുത്തമുള്ള ഉൽപ്പന്നങ്ങൾ കണ്ടെത്താനായില്ല."), data={"results": []})
    lines = []
    for item in top:
        price = float(item.get("offer_price") or item.get("price") or 0)
        description = str(item.get("description") or "").strip()
        line = f"{item.get('display_name', item.get('name', '')).title()} - Rs {price:.2f}"
        if description:
            line += f" | {description}"
        lines.append(line)
    return ToolObservation(tool="search_catalog", ok=True, message="\n".join(lines), data={"results": top, "products": [item.get("name", "") for item in top]})


def _tool_get_product_details(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    query = str(args.get("name") or args.get("query") or "").strip()
    product = get_product_details(query, business_id=business_id)
    if not product:
        return ToolObservation(tool="get_product_details", ok=False, message="", error=style_text(style, f"No product matched '{query}'.", f"'{query}' enna product kandilla.", f"'{query}' എന്ന ഉൽപ്പന്നം കണ്ടെത്താനായില്ല."))
    price = float(product.get("offer_price") or product.get("price") or 0)
    lines = [f"{product.get('display_name', product.get('name', '')).title()}", f"Price: Rs {price:.2f} per {product.get('unit', 'unit')}"]
    if product.get("description"):
        lines.append(str(product["description"]))
    if product.get("tags"):
        lines.append("Tags: " + ", ".join(product["tags"][:6]))
    return ToolObservation(tool="get_product_details", ok=True, message="\n".join(lines), data={"product": product, "products": [product.get("name", "")]})


def _tool_manage_cart(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    cart = dict(state.get("cart") or {"items": [], "address": ""})
    existing_items = list(cart.get("items", []))
    operation = str(args.get("operation") or "").strip().lower()
    query = str(args.get("query") or args.get("raw_message") or "").strip()
    profile = args.get("profile") or {}
    learning = args.get("learning") or {}

    if operation == "view":
        return ToolObservation(tool="manage_cart", ok=True, message=format_cart_message(style, existing_items, cart.get("address", "")), data={"items": existing_items, "products": [item.get("name", "") for item in existing_items]})
    raw_message = query or str(args.get("raw_message") or "").strip()
    signature_source = str(args.get("message_id") or "").strip()
    if not signature_source:
        raw_timestamp = str(args.get("message_timestamp") or args.get("timestamp") or "").strip()
        signature_window = "na"
        if raw_timestamp:
            try:
                parsed_timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
                if parsed_timestamp.tzinfo is None:
                    parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
                signature_window = str(int(parsed_timestamp.timestamp()) // 120)
            except ValueError:
                signature_window = raw_timestamp
        signature_source = f"{raw_message.lower()}::{signature_window or 'na'}::{operation or 'auto'}"
    action_signature = hashlib.sha1(signature_source.lower().encode("utf-8")).hexdigest()
    cart_meta = dict(state.get("cart_meta") or {})
    if action_signature and cart_meta.get("last_action_signature") == action_signature:
        return ToolObservation(
            tool="manage_cart",
            ok=True,
            message=format_cart_message(style, existing_items, cart.get("address", "")),
            data={"items": existing_items, "products": [item.get("name", "") for item in existing_items], "idempotent_skip": True},
        )

    parsed_requests = parse_cart_requests(raw_message)
    if not parsed_requests:
        return ToolObservation(tool="manage_cart", ok=False, message="", error=style_text(style, "I could not extract any valid cart items from that message. Send the product name with quantity, for example '2 boots'.", "I could not extract any valid cart items from that message. Send the product name with quantity, for example '2 boots'.", "I could not extract any valid cart items from that message. Send the product name with quantity, for example '2 boots'."), data={})

    current_cart = {"items": existing_items, "address": cart.get("address", "")}
    missing: List[str] = []
    resolved_product_names: List[str] = []

    for request in parsed_requests:
        parsed_action = operation if operation in {"add", "remove", "set", "clear"} else str(request.get("action") or "add")
        parsed_item = str(request.get("item") or "").strip()
        parsed_quantity = max(0, int(request.get("qty") or 0))
        explicit_qty = bool(request.get("explicit_qty"))
        logger.debug(
            "cart.manage parsed_action=%r parsed_item=%r parsed_quantity=%r cart_before=%r",
            parsed_action,
            parsed_item,
            parsed_quantity,
            current_cart,
        )

        if parsed_action == "clear":
            current_cart = apply_cart_action(current_cart, "", "clear", 0)
            logger.debug("cart.manage cart_after=%r", current_cart)
            continue

        if not parsed_item:
            missing.append(parsed_item or raw_message)
            continue

        resolved_items, unresolved = _resolve_items([{"product_name": parsed_item, "quantity": parsed_quantity}], business_id, profile, learning)
        if not resolved_items:
            missing.extend(unresolved or [parsed_item])
            continue

        resolved_name = normalize_product_name(resolved_items[0]["name"])
        current_qty = next((int(row.get("qty") or 0) for row in current_cart.get("items", []) if normalize_product_name(row.get("name", "")) == resolved_name), 0)
        effective_qty = parsed_quantity
        if parsed_action == "remove" and not explicit_qty:
            effective_qty = current_qty
        current_cart = apply_cart_action(current_cart, resolved_name, parsed_action, effective_qty)
        resolved_product_names.append(resolved_name)
        logger.debug("cart.manage cart_after=%r", current_cart)

    updated_items = [{"name": normalize_product_name(item.get("name", "")), "qty": int(item.get("qty") or 0)} for item in current_cart.get("items", []) if int(item.get("qty") or 0) > 0]
    if not updated_items and current_cart.get("address"):
        current_cart["address"] = cart.get("address", "")
    stage = "collect_address" if updated_items and not cart.get("address") else ("ready_to_checkout" if updated_items else "")
    state_patch = {
        "cart": {"items": updated_items, "address": cart.get("address", ""), "updated_at": utc_now().isoformat()},
        "cart_meta": {"last_action_signature": action_signature, "last_action_at": utc_now().isoformat()},
        "workflow": {
            "name": "order_checkout" if updated_items else "",
            "stage": stage,
            "required_slots": ["items", "address"] if updated_items else [],
            "filled_slots": {"items": bool(updated_items), "address": bool(cart.get("address"))},
            "next_question": "" if cart.get("address") or not updated_items else "Please share your delivery address when you're ready.",
        },
    }
    message = format_cart_message(style, updated_items, cart.get("address", ""))
    if updated_items and not cart.get("address"):
        message += "\n" + ask_address_text(style)
    if missing:
        warning = f"I could not match: {', '.join(missing)}"
        message += "\n" + style_text(style, warning, warning, warning)
    for product_name in resolved_product_names:
        record_recommendation_feedback(user["phone"], business_id, product_name=product_name, event="clicked")
    return ToolObservation(tool="manage_cart", ok=True, message=message, data={"items": updated_items, "products": [item["name"] for item in updated_items], "missing_products": missing, "partial_success": bool(missing)}, state_patch=state_patch, events=[{"kind": "cart_updated", "payload": {"items": updated_items}}])


def _tool_collect_address_legacy_removed(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    cart = dict(state.get("cart") or {"items": [], "address": ""})
    address = str(args.get("address") or args.get("raw_message") or "").strip()
    if len(address.split()) < 3:
        return ToolObservation(tool="collect_address", ok=False, message="", error=style_text(style, "Address is too short or incomplete.", "Address kurachu incomplete aanu.", "വിലാസം വളരെ ചെറിയതോ അപൂർണ്ണമോ ആണ്."))
    cart["address"] = address
    cart["updated_at"] = utc_now().isoformat()
    items = cart.get("items", [])
    state_patch = {
        "cart": cart,
        "workflow": {
            "name": "order_checkout" if items else "",
            "stage": "ready_to_checkout" if items else "idle",
            "required_slots": ["items", "address"] if items else [],
            "filled_slots": {"items": bool(items), "address": True},
            "next_question": "" if items else "Tell me what you'd like to order.",
        },
    }
    message = style_text(style, "Address noted.", "Address note cheythu.", "വിലാസം രേഖപ്പെടുത്തി.")
    if items:
        preview = build_order_preview(items, address, style=style, business_id=user.get("business_id", DEFAULT_BUSINESS_ID))
        message = preview["message"]
    return ToolObservation(tool="collect_address", ok=True, message=message, data={"address": address, "items": items}, state_patch=state_patch)


def _tool_checkout_order(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    cart = dict(state.get("cart") or {})
    items = list(args.get("items") or cart.get("items") or [])
    address = str(args.get("address") or cart.get("address") or "").strip()
    if not items:
        return ToolObservation(tool="checkout_order", ok=False, message="", error=style_text(style, "There are no cart items to checkout.", "Checkout cheyyan cart-il items illa.", "Checkout ചെയ്യാൻ കാർട്ടിൽ ഇനങ്ങളില്ല."))
    if not address:
        return ToolObservation(tool="checkout_order", ok=False, message="", error=style_text(style, "Delivery address is still missing.", "Delivery address iniyum kittiyittilla.", "ഡെലിവറി വിലാസം ഇനിയും ലഭിച്ചിട്ടില്ല."))
    order = create_order(business_id, user["phone"], items, address)
    send_vendor_order_notification(order, business_id=business_id)
    record_event("order_created", business_id=business_id, phone=user["phone"], payload={"order_id": order.orderId, "total": order.total})
    for item in order.items:
        record_recommendation_feedback(user["phone"], business_id, product_name=item.name, event="ordered")
    state_patch = {"cart": {"items": [], "address": "", "updated_at": utc_now().isoformat()}, "workflow": {"name": "", "stage": "", "required_slots": [], "filled_slots": {}, "next_question": ""}, "last_order_id": order.orderId}
    message = order_placed_text(style, order.orderId, order.total, order.status, order.address)
    return ToolObservation(tool="checkout_order", ok=True, message=message, data={"order_id": order.orderId, "total": order.total, "products": [item.name for item in order.items]}, state_patch=state_patch)


def _tool_get_order_status(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    order_id = str(args.get("order_id") or state.get("last_order_id") or "").strip()
    order = get_order_by_id(order_id, business_id) if order_id else get_latest_order(user["phone"], business_id)
    if not order:
        return ToolObservation(tool="get_order_status", ok=False, message="", error=style_text(style, "No recent order was found for this number.", "Ee number-il recent order kandilla.", "ഈ നമ്പറിന് സമീപകാല ഓർഡർ ഒന്നും കണ്ടെത്താനായില്ല."))
    items = ", ".join(f"{item.name} x {item.qty}" for item in order.items)
    message = order_status_text(style, order.orderId, order.status, items, order.total)
    return ToolObservation(tool="get_order_status", ok=True, message=message, data={"order_id": order.orderId, "status": order.status, "products": [item.name for item in order.items]}, state_patch={"last_order_id": order.orderId})


def _tool_create_support_ticket(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    issue = str(args.get("issue") or args.get("raw_message") or "").strip()
    if not issue:
        return ToolObservation(tool="create_support_ticket", ok=False, message="", error=style_text(style, "Issue details are required.", "Issue detail venam.", "പ്രശ്നത്തിന്റെ വിശദാംശങ്ങൾ ആവശ്യമാണ്."))
    existing = get_collection("support_tickets").find_one({"business_id": business_id, "customer_phone": user["phone"], "status": {"$ne": "resolved"}}, sort=[("created_at", -1)])
    if existing:
        get_collection("support_tickets").update_one({"_id": existing["_id"]}, {"$set": {"issue": issue, "updated_at": utc_now()}})
        ticket_id = existing["ticket_id"]
        record_event("support_ticket_updated", business_id=business_id, phone=user["phone"], payload={"ticket_id": ticket_id})
        return ToolObservation(tool="create_support_ticket", ok=True, message=support_ticket_text(style, ticket_id, str(existing.get("priority", "normal")), updated=True), data={"ticket_id": ticket_id, "priority": existing.get("priority", "normal"), "status": existing.get("status", "open")}, state_patch={"open_ticket_id": ticket_id})
    ticket = create_support_ticket(business_id=business_id, customer_phone=user["phone"], issue=issue, order_id=str(args.get("order_id") or state.get("last_order_id") or "").strip() or None)
    record_event("support_ticket_created", business_id=business_id, phone=user["phone"], payload=ticket)
    return ToolObservation(tool="create_support_ticket", ok=True, message=support_ticket_text(style, ticket["ticket_id"], ticket["priority"]), data=ticket, state_patch={"open_ticket_id": ticket["ticket_id"]})


def _tool_recommend_products(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    cart_names = [normalize_product_name(item.get("name", "")) for item in (state.get("cart") or {}).get("items", [])]
    top = recommend_products(user["phone"], business_id, query=str(args.get("query") or ""), exclude=cart_names)
    if not top:
        return ToolObservation(tool="recommend_products", ok=True, message=style_text(style, "I don't have a strong recommendation right now.", "Ippo strong recommendation illa.", "ഇപ്പോൾ വ്യക്തമായ ശുപാർശ ഒന്നുമില്ല."), data={"results": []})
    lines = []
    for item in top[:4]:
        price = float(item.get("offer_price") or item.get("price") or 0)
        ranking = item.get("ranking", {})
        note = ""
        if ranking.get("affinity", 0) >= 0.15:
            note = " | matches your usual choices"
        elif ranking.get("exploration", 0) > 0:
            note = " | something new worth trying"
        lines.append(f"{item['display_name']} - Rs {price:.2f}{note}")
    for item in top[:4]:
        record_recommendation_feedback(user["phone"], business_id, product_name=str(item.get("name") or ""), event="shown")
    return ToolObservation(tool="recommend_products", ok=True, message=recommendation_intro(style) + "\n" + "\n".join(lines), data={"results": top, "products": [item.get("name", "") for item in top]})


def _tool_vendor_update_order_status(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    status = str(args.get("status") or "").strip().lower()
    order_id = str(args.get("order_id") or state.get("last_vendor_order_id") or "").strip() or None
    order = get_latest_vendor_actionable_order(order_id, status, business_id=business_id)
    if not order:
        return ToolObservation(tool="vendor_update_order_status", ok=False, message="", error=style_text(style, "I couldn't find a matching order to update.", "Update cheyyan pattiya order kandethan pattiyilla.", "അപ്‌ഡേറ്റ് ചെയ്യാൻ അനുയോജ്യമായ ഓർഡർ കണ്ടെത്താനായില്ല."))
    updated = update_order_status(order.orderId, status, business_id=business_id)
    if not updated:
        return ToolObservation(tool="vendor_update_order_status", ok=False, message="", error=style_text(style, f"Order {order.orderId} cannot move to {status}.", f"Order {order.orderId} {status}ilekku maattan pattilla.", f"Order {order.orderId} നെ {status_phrase(status, 'malayalam')} നിലയിലേക്ക് മാറ്റാൻ കഴിയില്ല."))
    notify_customer_order_update(updated.customerPhone, updated, style_hint=style, business_id=business_id)
    record_event("vendor_status_updated", business_id=business_id, phone=user["phone"], payload={"order_id": updated.orderId, "status": status})
    return ToolObservation(tool="vendor_update_order_status", ok=True, message=generate_vendor_acknowledgement(updated, style, status), data={"order_id": updated.orderId, "status": status}, state_patch={"last_vendor_order_id": updated.orderId})


def _tool_resolve_support_ticket(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    ticket_id = str(args.get("ticket_id") or state.get("open_ticket_id") or "").strip()
    resolution = str(args.get("resolution") or "Issue resolved").strip()
    if not ticket_id:
        return ToolObservation(tool="resolve_support_ticket", ok=False, message="", error=style_text(style, "Ticket ID is required.", "Ticket ID venam.", "Ticket ID ആവശ്യമാണ്."))
    ticket = get_collection("support_tickets").find_one({"business_id": user.get("business_id", DEFAULT_BUSINESS_ID), "ticket_id": ticket_id})
    if not ticket:
        return ToolObservation(tool="resolve_support_ticket", ok=False, message="", error=style_text(style, f"Ticket {ticket_id} was not found.", f"Ticket {ticket_id} kandilla.", f"Ticket {ticket_id} കണ്ടെത്താനായില്ല."))
    get_collection("support_tickets").update_one({"_id": ticket["_id"]}, {"$set": {"status": "resolved", "resolution_notes": resolution, "resolved_at": utc_now(), "updated_at": utc_now()}})
    from app.services.vendor import send_whatsapp_message
    customer_style = style
    send_whatsapp_message(ticket["customer_phone"], style_text(customer_style, f"Your issue has been resolved. Ticket {ticket_id}: {resolution}", f"Ningalude issue solve cheythu. Ticket {ticket_id}: {resolution}", f"നിങ്ങളുടെ പ്രശ്നം പരിഹരിച്ചു. Ticket {ticket_id}: {resolution}"), from_number=get_business_profile(user.get("business_id", DEFAULT_BUSINESS_ID)).get("whatsapp_number"))
    record_event("support_ticket_resolved", business_id=user.get("business_id", DEFAULT_BUSINESS_ID), phone=ticket["customer_phone"], payload={"ticket_id": ticket_id})
    return ToolObservation(tool="resolve_support_ticket", ok=True, message=style_text(style, f"Ticket {ticket_id} resolved.", f"Ticket {ticket_id} resolve cheythu.", f"Ticket {ticket_id} പരിഹരിച്ചു."), data={"ticket_id": ticket_id}, state_patch={"open_ticket_id": ""})


def _tool_manage_product(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    operation = str(args.get("operation") or "update").strip().lower()
    name = str(args.get("name") or "").strip()
    if operation == "add":
        product = add_product(business_id=business_id, name=name, price=float(args.get("price") or 0), unit=str(args.get("unit") or "unit"), category=str(args.get("category") or "").strip() or None, description=str(args.get("description") or ""), tags=list(args.get("tags") or []), attributes=dict(args.get("attributes") or {}))
        return ToolObservation(tool="manage_product", ok=True, message=style_text(style, f"Added {product['display_name']}.", f"{product['display_name']} add cheythu.", f"{product['display_name']} ചേർത്തു."), data={"product": product})
    if operation == "delete":
        deleted = delete_product(name, business_id=business_id)
        return ToolObservation(tool="manage_product", ok=deleted, message=style_text(style, f"Removed {name}.", f"{name} remove cheythu.", f"{name} നീക്കം ചെയ്തു.") if deleted else "", error="" if deleted else style_text(style, f"Product {name} not found.", f"Product {name} kandilla.", f"Product {name} കണ്ടെത്താനായില്ല."))
    product = update_product(business_id=business_id, name=name, price=float(args["price"]) if args.get("price") is not None else None, unit=args.get("unit"), category=args.get("category"), description=args.get("description"), tags=args.get("tags"), attributes=args.get("attributes"), active=args.get("active"))
    if not product:
        return ToolObservation(tool="manage_product", ok=False, message="", error=style_text(style, f"Product {name} not found.", f"Product {name} kandilla.", f"Product {name} കണ്ടെത്താനായില്ല."))
    return ToolObservation(tool="manage_product", ok=True, message=style_text(style, f"Updated {product['display_name']}.", f"{product['display_name']} update cheythu.", f"{product['display_name']} അപ്‌ഡേറ്റ് ചെയ്തു."), data={"product": product})


def _tool_create_offer(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    product = update_product_offer(str(args.get("product") or "").strip(), float(args.get("price") or 0), str(args.get("date") or "").strip() or None, business_id=business_id)
    message = style_text(style, f"Offer updated for {product['display_name']}: Rs {float(product.get('offer_price') or product.get('price') or 0):.2f}", f"{product['display_name']} offer update cheythu: Rs {float(product.get('offer_price') or product.get('price') or 0):.2f}", f"{product['display_name']} ഓഫർ അപ്‌ഡേറ്റ് ചെയ്തു: Rs {float(product.get('offer_price') or product.get('price') or 0):.2f}")
    return ToolObservation(tool="create_offer", ok=True, message=message, data={"product": product})


def _tool_create_promotion(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    promotion = create_promotion(business_id=user.get("business_id", DEFAULT_BUSINESS_ID), title=str(args.get("title") or "").strip(), scope=str(args.get("scope") or "global").strip(), target=str(args.get("target") or "").strip() or None, discount_type=str(args.get("discount_type") or "percent").strip(), discount_value=float(args.get("discount_value") or 0), starts_at=str(args.get("starts_at") or "").strip() or None, ends_at=str(args.get("ends_at") or "").strip() or None, broadcast_text=str(args.get("broadcast_text") or "").strip() or None)
    return ToolObservation(tool="create_promotion", ok=True, message=style_text(style, f"Promotion created: {promotion['title']}.", f"Promotion create cheythu: {promotion['title']}.", f"Promotion സൃഷ്ടിച്ചു: {promotion['title']}."), data={"promotion": promotion})


def _tool_create_event(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    event = create_event(business_id=user.get("business_id", DEFAULT_BUSINESS_ID), title=str(args.get("title") or "").strip(), description=str(args.get("description") or "").strip(), starts_at=str(args.get("starts_at") or "").strip() or None, ends_at=str(args.get("ends_at") or "").strip() or None, promotion_scope=str(args.get("promotion_scope") or "").strip() or None, promotion_target=str(args.get("promotion_target") or "").strip() or None, discount_type=str(args.get("discount_type") or "").strip() or None, discount_value=float(args["discount_value"]) if args.get("discount_value") is not None else None, broadcast_text=str(args.get("broadcast_text") or "").strip() or None)
    return ToolObservation(tool="create_event", ok=True, message=style_text(style, f"Event created: {event['title']}.", f"Event create cheythu: {event['title']}.", f"Event സൃഷ്ടിച്ചു: {event['title']}."), data={"event": event})


def _tool_update_business_profile(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    profile = update_business_profile(business_id=user.get("business_id", DEFAULT_BUSINESS_ID), name=args.get("name"), business_type=args.get("business_type"), description=args.get("description"), address=args.get("address"), hours=args.get("hours"), contact_phone=args.get("contact_phone"), support_details=args.get("support_details"), policies=args.get("policies"), delivery_info=args.get("delivery_info"), broadcast_groups=args.get("broadcast_groups"))
    return ToolObservation(tool="update_business_profile", ok=True, message=style_text(style, "Business profile updated.", "Business profile update cheythu.", "Business profile അപ്‌ഡേറ്റ് ചെയ്തു."), data={"profile": profile})


def _tool_send_broadcast(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    message = str(args.get("message") or "").strip()
    if not message:
        return ToolObservation(tool="send_broadcast", ok=False, message="", error=style_text(style, "Broadcast message is required.", "Broadcast message venam.", "Broadcast message ആവശ്യമാണ്."))
    targets = get_customer_broadcast_targets(business_id=business_id)
    result = execute_broadcast(message, targets, from_number=get_business_profile(business_id).get("whatsapp_number") or "")
    record_event("broadcast_sent", business_id=business_id, phone=user["phone"], payload={"sent": result.get("sent", 0)})
    return ToolObservation(tool="send_broadcast", ok=True, message=style_text(style, f"Broadcast sent to {result.get('sent', 0)} recipients.", f"Broadcast {result.get('sent', 0)} recipients-kku ayachu.", f"Broadcast {result.get('sent', 0)} പേർക്ക് അയച്ചു."), data=result)


def _resolve_items(items: List[Dict[str, Any]], business_id: str, profile: Dict[str, Any], learning: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[str]]:
    catalog = get_product_catalog(business_id=business_id, limit=100)
    exact_map = {str(item.get("name") or "").strip().lower(): item for item in catalog}
    normalized_map = {normalize_product_name(str(item.get("name") or "")): item for item in catalog}
    resolved: List[Dict[str, Any]] = []
    missing: List[str] = []
    for item in items:
        requested = str(item.get("product_name") or item.get("name") or item.get("product") or "").strip()
        qty = max(1, int(item.get("qty") or item.get("quantity") or 1))
        if not requested:
            continue
        lowered = requested.lower()
        product = exact_map.get(lowered) or get_product_details(requested, business_id=business_id)
        if product:
            resolved.append({"name": product["name"], "qty": qty})
            continue
        normalized_product = normalized_map.get(normalize_product_name(requested))
        if normalized_product:
            resolved.append({"name": normalized_product["name"], "qty": qty})
            continue
        ranked = rank_products(requested, catalog, profile=profile, learning_profile=learning)
        best_ranked = max(ranked, key=lambda row: float(((row.get("ranking") or {}).get("score")) or 0.0), default=None)
        if best_ranked and float(((best_ranked.get("ranking") or {}).get("score")) or 0.0) >= 0.24:
            resolved.append({"name": best_ranked["name"], "qty": qty})
        else:
            missing.append(requested)
    return resolved, missing


def _llm_parse_cart_request(query: str, catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    shortlist = [{"name": product.get("name"), "display_name": product.get("display_name"), "tags": product.get("tags", [])[:4]} for product in catalog[:30]]
    try:
        payload = call_agent_llm(
            [
                {"role": "system", "content": "Extract order items from the user message using only products from the shortlist. Return JSON with action='manage_cart', arguments={items:[{name,qty}]}, message='', confidence."},
                {"role": "user", "content": str({"message": query, "catalog_shortlist": shortlist})},
            ],
            tier="cheap",
        )
    except Exception:
        return []
    if isinstance(payload, str):
        try:
            import json

            payload = json.loads(payload)
        except Exception:
            return []
    items = payload.get("arguments", {}).get("items")
    if not isinstance(items, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            qty = max(1, int(item.get("qty", 1)))
        except (TypeError, ValueError):
            qty = 1
        normalized.append({"name": name, "qty": qty})
    return normalized


def vendor_status_candidates(message: str, business_id: str, learning_profile: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    scores = Counter()
    lowered = message.lower()
    descriptions = {
        "accepted": "accepted confirmed approved taking order",
        "rejected": "rejected cancelled cannot fulfill unavailable",
        "out_for_delivery": "dispatch on the way sent rider left",
        "delivered": "delivered reached completed handed over done",
    }
    for status, description in descriptions.items():
        ranked = rank_products(lowered, [{"name": status, "display_name": status, "description": description, "tags": description.split()}], profile={}, learning_profile={})
        if ranked:
            scores[status] += float(ranked[0]["ranking"]["score"]) * 2.0
    learned = (learning_profile or {}).get("status_patterns", {})
    for status, weights in learned.items():
        if not isinstance(weights, dict):
            continue
        for token in lowered.split():
            scores[status] += float(weights.get(token, 0)) / 10.0
    hints = {"accepted": ["accept", "okay", "ok", "confirmed"], "rejected": ["reject", "cancel", "cannot", "cant"], "out_for_delivery": ["dispatch", "sending", "on the way"], "delivered": ["delivered", "done", "completed", "reached"]}
    for status, tokens in hints.items():
        for token in tokens:
            if token in lowered:
                scores[status] += 0.7
    ranked = [{"status": status, "score": round(score, 3)} for status, score in scores.items() if score > 0]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def _looks_like_address(message: str) -> bool:
    raw = str(message or "").strip()
    lowered = raw.lower()
    if not lowered:
        return False
    conversational = {
        "no",
        "wait",
        "i mean",
        "not that",
        "wrong",
        "change it",
        "hold on",
        "later",
        "okay",
        "ok",
        "yes",
    }
    if lowered in conversational or any(lowered.startswith(prefix) for prefix in {"no ", "wait ", "i mean ", "change "}):
        return False
    segments = [segment.strip() for segment in re.split(r"[,\n]", raw) if segment.strip()]
    tokens = [token for token in re.split(r"\s+", raw) if token.strip()]
    if len(segments) < 2 and len(tokens) < 2:
        return False
    address_keywords = {"road", "rd", "street", "st", "lane", "house", "building", "floor", "flat", "near", "pin", "city", "district", "post", "po", "junction", "p.o", "villa", "block", "kur", "nagar"}
    has_keyword = any(token.strip(".,").lower() in address_keywords for token in tokens)
    has_number = any(any(ch.isdigit() for ch in token) for token in tokens)
    has_capitalized = any(token[:1].isupper() and len(token) > 1 for token in raw.split())
    return (len(segments) >= 2 or len(tokens) >= 3) and (has_keyword or has_number or has_capitalized)


def _tool_collect_address(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    cart = dict(state.get("cart") or {"items": [], "address": ""})
    address = str(args.get("address") or args.get("raw_message") or "").strip()
    if not _looks_like_address(address):
        return ToolObservation(
            tool="collect_address",
            ok=False,
            message="",
            error=style_text(style, "That doesn't look like a full delivery address yet. Please send the house/building, area, and landmark or PIN.", "Ithu full delivery address pole thonnunnilla. House/building, area, landmark allenkil PIN ayakku.", "ഇത് പൂർണ്ണമായ ഡെലിവറി വിലാസമായി തോന്നുന്നില്ല. വീട്/ബിൽഡിംഗ്, ഏരിയ, ലാൻഡ്മാർക്ക് അല്ലെങ്കിൽ പിൻ അയയ്ക്കൂ."),
        )
    cart["address"] = address
    cart["updated_at"] = utc_now().isoformat()
    items = cart.get("items", [])
    state_patch = {
        "cart": cart,
        "workflow": {
            "name": "order_checkout" if items else "",
            "stage": "ready_to_checkout" if items else "idle",
            "required_slots": ["items", "address"] if items else [],
            "filled_slots": {"items": bool(items), "address": True},
            "next_question": "" if items else "Tell me what you'd like to order.",
        },
    }
    message = style_text(style, "Address noted.", "Address note cheythu.", "വിലാസം രേഖപ്പെടുത്തി.")
    if items:
        preview = build_order_preview(items, address, style=style, business_id=user.get("business_id", DEFAULT_BUSINESS_ID))
        message = preview["message"]
    return ToolObservation(tool="collect_address", ok=True, message=message, data={"address": address, "items": items}, state_patch=state_patch)


def _tool_repair_order_context(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    cart = dict(state.get("cart") or {"items": [], "address": ""})
    raw_message = str(args.get("raw_message") or "").strip()
    lowered = raw_message.lower()
    if any(phrase in lowered for phrase in {"address", "wrong", "change", "not my address", "thats not", "that's not"}):
        return ToolObservation(
            tool="repair_order_context",
            ok=True,
            message=ask_address_text(style),
            data={"recovery_signal": True, "products": [item.get("name", "") for item in cart.get("items", [])]},
            state_patch={
                "cart": {"items": cart.get("items", []), "address": "", "updated_at": utc_now().isoformat()},
                "workflow": {
                    "name": "order_checkout" if cart.get("items") else "",
                    "stage": "collect_address" if cart.get("items") else "",
                    "required_slots": ["items", "address"] if cart.get("items") else [],
                    "filled_slots": {"items": bool(cart.get("items")), "address": False},
                    "next_question": "Please share your correct delivery address." if cart.get("items") else "",
                },
            },
        )
    return ToolObservation(
        tool="repair_order_context",
        ok=True,
        message=style_text(style, "I understood that as a correction. Tell me what should be fixed in the order.", "Ithu correction aanu ennu manassilayi. Order-il entha fix cheyyendathu parayu.", "ഇത് ഒരു തിരുത്തലാണെന്ന് മനസ്സിലായി. ഓർഡറിൽ എന്ത് ശരിയാക്കണമെന്ന് പറയൂ."),
        data={"recovery_signal": True, "products": [item.get("name", "") for item in cart.get("items", [])]},
        state_patch={"workflow": {"name": "order_checkout" if cart.get("items") else "", "stage": "collect_address" if cart.get("items") else "", "required_slots": ["items", "address"] if cart.get("items") else [], "filled_slots": {"items": bool(cart.get("items")), "address": bool(cart.get("address"))}, "next_question": "Clarify what should be corrected."}},
    )


def _is_generic_browse_query(query: str) -> bool:
    lowered = str(query or "").lower().strip()
    if not lowered:
        return True
    generic_phrases = {
        "what products do you have",
        "show products",
        "show me products",
        "show me what products you have",
        "show me what you have",
        "i want to see products",
        "i want to see the products",
        "i want to see all the products",
        "what do you have",
        "what all do you have",
        "available products",
        "show catalog",
        "show menu",
        "products",
        "catalog",
        "menu",
    }
    return lowered in generic_phrases or "products do you have" in lowered or "what do you have" in lowered or ("products" in lowered and any(term in lowered for term in {"show", "see", "browse"}))


def _tool_search_catalog(args: Dict[str, Any], state: Dict[str, Any], user: Dict[str, Any], style: str) -> ToolObservation:
    business_id = user.get("business_id", DEFAULT_BUSINESS_ID)
    query = str(args.get("query") or args.get("search") or "").strip()
    generic_browse = _is_generic_browse_query(query)
    profile = args.get("profile") or {}
    learning = args.get("learning") or {}
    products = get_product_catalog(
        business_id=business_id,
        filter_category=args.get("category"),
        search=None if generic_browse else (query or None),
        limit=min(max(int(args.get("limit", 8) or 8), 1), 12),
    )
    ranked = rank_products("products" if generic_browse else (query or "products"), products, profile=profile, learning_profile=learning)
    top = ranked[:5]
    if not top and query and not generic_browse:
        broad_products = get_product_catalog(
            business_id=business_id,
            filter_category=args.get("category"),
            search=None,
            limit=min(max(int(args.get("limit", 8) or 8), 1), 12),
        )
        broad_query = _broaden_catalog_query(query)
        broad_ranked = rank_products(broad_query, broad_products, profile=profile, learning_profile=learning)
        if broad_ranked:
            top = broad_ranked[:5]
            generic_browse = True
    if not top:
        return ToolObservation(
            tool="search_catalog",
            ok=True,
            message=style_text(style, "I couldn't find an exact match. Tell me a category or product type and I will narrow it down.", "Exact match kandilla. Category allenkil product type paranjal njan narrow cheyyam.", "Exact match kandilla. Category allenkil product type paranjal njan narrow cheyyam."),
            data={"results": [], "products": [], "recovery_needed": True},
        )
    header = style_text(style, "Here are some products you can browse:", "Browse cheyyan pattunna kurachu products ivide undu:", "Browse cheyyan pattunna kurachu products ivide undu:") if generic_browse else style_text(style, "Here are some products you can order:", "Order cheyyan pattunna kurachu products ivide undu:", "ഓർഡർ ചെയ്യാൻ കഴിയുന്ന ചില ഉൽപ്പന്നങ്ങൾ ഇവയാണ്:")
    lines = [header]
    for item in top:
        price = float(item.get("offer_price") or item.get("price") or 0)
        description = str(item.get("description") or "").strip()
        line = f"{item.get('display_name', item.get('name', '')).title()} - Rs {price:.2f}"
        if description:
            line += f" | {description}"
        lines.append(line)
    return ToolObservation(tool="search_catalog", ok=True, message="\n".join(lines), data={"results": top, "products": [item.get("name", "") for item in top], "generic_browse": generic_browse})


def _broaden_catalog_query(query: str) -> str:
    lowered = str(query or "").lower()
    stop_words = {"show", "me", "what", "do", "you", "have", "i", "want", "to", "see", "all", "the", "products", "product", "browse", "please", "can"}
    tokens = [token for token in lowered.replace("?", " ").replace(",", " ").split() if token and token not in stop_words]
    return " ".join(tokens) or "products"
