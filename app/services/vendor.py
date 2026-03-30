import asyncio
import json
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from app.core.settings import get_settings
from app.db.mongo import DEFAULT_BUSINESS_ID, generate_id, get_collection, parse_datetime, slugify, to_iso, utc_now
from app.services.agent.localization import contact_style, status_phrase, style_text
from app.services.messaging.provider import get_messaging_provider
from app.services.messaging.service import (
    enforce_messaging_policy,
    mark_outbound_failure,
    mark_outbound_sent,
    reserve_outbound_message,
    retryable_dispatches,
)
from app.services.messaging.utils import normalize_phone
from app.models.order import OrderRecord


def _log_vendor(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[vendor] {event} {normalized}".rstrip())


def send_whatsapp_message(
    to: str,
    body: str,
    from_number: Optional[str] = None,
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    notification_type: str = "message",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_to = normalize_phone(to)
    business_profile = get_business_profile(business_id)
    normalized_from = normalize_phone(from_number or str(business_profile.get("whatsapp_number") or ""))
    provider = get_messaging_provider()
    dispatch = reserve_outbound_message(
        business_id=business_id,
        to=normalized_to,
        from_number=normalized_from,
        body=body,
        notification_type=notification_type,
        metadata=metadata,
    )
    dispatch_doc = dispatch["document"]
    if dispatch.get("duplicate"):
        return {
            "success": str(dispatch_doc.get("status") or "") in {"sent", "delivered", "read"},
            "duplicate": True,
            "provider": str(dispatch_doc.get("provider") or "meta"),
            "message_id": str(dispatch_doc.get("message_id") or ""),
            "status": str(dispatch_doc.get("status") or ""),
            "template_required": str(dispatch_doc.get("status") or "") == "template_required",
        }
    _log_vendor(
        "send_whatsapp_message_start",
        provider=getattr(provider, "provider_name", "meta"),
        to=normalized_to,
        from_number=normalized_from,
        body_preview=body[:240],
        debug_mode=get_settings().debug_mode,
    )
    if get_settings().debug_mode:
        print("=== DEBUG WHATSAPP MESSAGE ===")
        print(f"FROM: {normalized_from}")
        print(f"TO: {normalized_to}")
        print(body)
        print("==============================")
        return {"success": True, "debug": True, "provider": "debug"}
    policy = enforce_messaging_policy(
        business_id=business_id,
        to=normalized_to,
        notification_type=notification_type,
        dispatch_id=dispatch_doc["_id"],
    )
    if not policy.get("allowed"):
        return {"success": False, "provider": "meta", "status": policy["reason"], "template_required": policy["reason"] == "template_required"}

    try:
        result = provider.send_text(normalized_to, body, from_number=normalized_from)
        mark_outbound_sent(dispatch_doc["_id"], result)
        _log_vendor(
            "send_whatsapp_message_complete",
            provider=result.get("provider", getattr(provider, "provider_name", "")),
            success=result.get("success"),
            to=normalized_to,
            message_id=result.get("message_id") or result.get("sid"),
        )
        return result
    except Exception as exc:
        failure = mark_outbound_failure(dispatch_doc["_id"], exc)
        _log_vendor(
            "send_whatsapp_message_failed",
            provider=getattr(provider, "provider_name", "meta"),
            to=normalized_to,
            error=str(exc),
        )
        return {"success": False, "error": str(exc), "queued": failure["retryable"], "provider": "meta"}


async def send_whatsapp_message_async(
    to: str,
    body: str,
    from_number: Optional[str] = None,
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    notification_type: str = "message",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        send_whatsapp_message,
        to,
        body,
        from_number,
        business_id=business_id,
        notification_type=notification_type,
        metadata=metadata,
    )


def normalize_product_name(name: str) -> str:
    normalized = re.sub(r"\s+", " ", (name or "").lower()).strip()
    if "banana" in normalized and "chip" in normalized:
        return "banana chips"
    if "soft drink" in normalized:
        return "cold drink"
    return normalized


def get_business_by_id(business_id: str) -> Optional[Dict[str, Any]]:
    document = get_collection("businesses").find_one({"business_id": business_id})
    return _serialize_business(document) if document else None


def get_business_by_whatsapp_number(number: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_phone(number)
    if not normalized:
        return None
    document = get_collection("businesses").find_one({"whatsapp_number": normalized, "active": True})
    return _serialize_business(document) if document else None


def get_business_by_vendor_phone(number: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_phone(number)
    if not normalized:
        return None
    document = get_collection("businesses").find_one({"vendor_phone": normalized, "active": True})
    return _serialize_business(document) if document else None


def list_businesses(active_only: bool = False) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {}
    if active_only:
        query["active"] = True
    return [_serialize_business(doc) for doc in get_collection("businesses").find(query).sort("created_at", 1)]


def create_business(
    *,
    name: str,
    slug: Optional[str] = None,
    whatsapp_number: str,
    vendor_phone: str,
    business_type: str = "retail",
    description: str = "",
    address: str = "",
    hours: str = "",
    contact_phone: str = "",
    support_details: str = "",
    policies: str = "",
    delivery_info: str = "",
    messaging_rate_limit_per_minute: int = 30,
    automation_cooldown_minutes: int = 180,
    automation_max_messages_per_day: int = 2,
) -> Dict[str, Any]:
    now = utc_now()
    normalized_whatsapp = normalize_phone(whatsapp_number)
    normalized_vendor = normalize_phone(vendor_phone)
    normalized_contact = normalize_phone(contact_phone) if contact_phone.strip() else normalized_vendor
    normalized_slug = slugify(slug or name)

    reusable = _find_reusable_business(normalized_vendor, normalized_whatsapp, normalized_slug)
    if reusable:
        document = get_collection("businesses").find_one_and_update(
            {"_id": reusable["_id"]},
            {
                "$set": {
                    "slug": normalized_slug,
                    "name": name.strip(),
                    "whatsapp_number": normalized_whatsapp,
                    "vendor_phone": normalized_vendor,
                    "business_type": business_type.strip().lower() or "retail",
                    "description": description.strip(),
                    "address": address.strip(),
                    "hours": hours.strip(),
                    "contact_phone": normalized_contact,
                    "support_details": support_details.strip(),
                    "policies": policies.strip(),
                    "delivery_info": delivery_info.strip(),
                    "messaging_rate_limit_per_minute": max(1, int(messaging_rate_limit_per_minute)),
                    "automation_cooldown_minutes": max(5, int(automation_cooldown_minutes)),
                    "automation_max_messages_per_day": max(1, int(automation_max_messages_per_day)),
                    "active": True,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _serialize_business(document)

    _release_inactive_business_conflicts(
        vendor_phone=normalized_vendor,
        whatsapp_number=normalized_whatsapp,
        slug=normalized_slug,
    )
    _ensure_business_conflicts_clear(normalized_vendor, normalized_whatsapp, normalized_slug)
    business_id = generate_id("biz")
    document = {
        "business_id": business_id,
        "slug": normalized_slug,
        "name": name.strip(),
        "whatsapp_number": normalized_whatsapp,
        "vendor_phone": normalized_vendor,
        "business_type": business_type.strip().lower() or "retail",
        "description": description.strip(),
        "address": address.strip(),
        "hours": hours.strip(),
        "contact_phone": normalized_contact,
        "support_details": support_details.strip(),
        "policies": policies.strip(),
        "delivery_info": delivery_info.strip(),
        "messaging_rate_limit_per_minute": max(1, int(messaging_rate_limit_per_minute)),
        "automation_cooldown_minutes": max(5, int(automation_cooldown_minutes)),
        "automation_max_messages_per_day": max(1, int(automation_max_messages_per_day)),
        "broadcast_groups": [],
        "active": True,
        "created_at": now,
        "updated_at": now,
    }
    get_collection("businesses").insert_one(document)
    return _serialize_business(document)


def update_business(
    business_id: str,
    *,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    whatsapp_number: Optional[str] = None,
    vendor_phone: Optional[str] = None,
    business_type: Optional[str] = None,
    description: Optional[str] = None,
    address: Optional[str] = None,
    hours: Optional[str] = None,
    contact_phone: Optional[str] = None,
    support_details: Optional[str] = None,
    policies: Optional[str] = None,
    delivery_info: Optional[str] = None,
    messaging_rate_limit_per_minute: Optional[int] = None,
    automation_cooldown_minutes: Optional[int] = None,
    automation_max_messages_per_day: Optional[int] = None,
    active: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    current = get_collection("businesses").find_one({"business_id": business_id})
    if not current:
        return None

    update_fields: Dict[str, Any] = {"updated_at": utc_now()}
    if name is not None:
        update_fields["name"] = name.strip()
    if slug is not None:
        update_fields["slug"] = slugify(slug)
    if whatsapp_number is not None:
        update_fields["whatsapp_number"] = normalize_phone(whatsapp_number)
    if vendor_phone is not None:
        update_fields["vendor_phone"] = normalize_phone(vendor_phone)
    if business_type is not None:
        update_fields["business_type"] = business_type.strip().lower() or "retail"
    if description is not None:
        update_fields["description"] = description.strip()
    if address is not None:
        update_fields["address"] = address.strip()
    if hours is not None:
        update_fields["hours"] = hours.strip()
    if contact_phone is not None:
        update_fields["contact_phone"] = normalize_phone(contact_phone) if contact_phone.strip() else ""
    if support_details is not None:
        update_fields["support_details"] = support_details.strip()
    if policies is not None:
        update_fields["policies"] = policies.strip()
    if delivery_info is not None:
        update_fields["delivery_info"] = delivery_info.strip()
    if messaging_rate_limit_per_minute is not None:
        update_fields["messaging_rate_limit_per_minute"] = max(1, int(messaging_rate_limit_per_minute))
    if automation_cooldown_minutes is not None:
        update_fields["automation_cooldown_minutes"] = max(5, int(automation_cooldown_minutes))
    if automation_max_messages_per_day is not None:
        update_fields["automation_max_messages_per_day"] = max(1, int(automation_max_messages_per_day))
    if active is not None:
        update_fields["active"] = bool(active)

    target_slug = str(update_fields.get("slug") or current.get("slug") or "").strip()
    target_vendor = str(update_fields.get("vendor_phone") or current.get("vendor_phone") or "").strip()
    target_whatsapp = str(update_fields.get("whatsapp_number") or current.get("whatsapp_number") or "").strip()

    _release_inactive_business_conflicts(
        current_business_id=business_id,
        vendor_phone=target_vendor,
        whatsapp_number=target_whatsapp,
        slug=target_slug,
    )
    _ensure_business_conflicts_clear(
        target_vendor,
        target_whatsapp,
        target_slug,
        exclude_business_id=business_id,
    )

    document = get_collection("businesses").find_one_and_update(
        {"business_id": business_id},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize_business(document) if document else None


def get_product_category(name: str, business_id: str = DEFAULT_BUSINESS_ID) -> str:
    product = get_product_details(name, business_id=business_id)
    if product:
        return product.get("category", "other")
    normalized = normalize_product_name(name)
    if any(token in normalized for token in ["drink", "juice", "coffee", "tea", "milk", "coke"]):
        return "drink"
    if any(token in normalized for token in ["shoe", "sandal", "slipper", "boot"]):
        return "footwear"
    if any(token in normalized for token in ["shirt", "dress", "pant", "jean", "clothing", "kurti", "tshirt"]):
        return "clothing"
    if any(token in normalized for token in ["chip", "snack", "sandwich"]):
        return "snack"
    return "other"


def get_product_catalog(
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    filter_category: Optional[str] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    tags: Optional[List[str]] = None,
    sort_by: str = "display_name",
    sort_order: str = "asc",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"business_id": business_id, "active": True}
    if filter_category:
        query["category"] = filter_category
    if search:
        query["$or"] = [
            {"display_name": {"$regex": re.escape(search), "$options": "i"}},
            {"name": {"$regex": re.escape(normalize_product_name(search)), "$options": "i"}},
            {"description": {"$regex": re.escape(search), "$options": "i"}},
            {"tags": {"$elemMatch": {"$regex": re.escape(search), "$options": "i"}}},
        ]
    if min_price is not None or max_price is not None:
        price_query: Dict[str, Any] = {}
        if min_price is not None:
            price_query["$gte"] = float(min_price)
        if max_price is not None:
            price_query["$lte"] = float(max_price)
        query["price"] = price_query
    if tags:
        normalized_tags = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
        if normalized_tags:
            query["tags"] = {"$in": normalized_tags}

    allowed_sort_fields = {"display_name", "price", "category", "updated_at"}
    mongo_sort_field = sort_by if sort_by in allowed_sort_fields else "display_name"
    mongo_sort_order = 1 if str(sort_order).lower() != "desc" else -1
    safe_limit = min(max(int(limit or 10), 1), 100)
    cursor = get_collection("products").find(query).sort(mongo_sort_field, mongo_sort_order).limit(safe_limit)
    return [_serialize_product(doc) for doc in cursor]


def get_product_catalog_map(business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Dict[str, Any]]:
    return {product["name"]: product for product in get_product_catalog(business_id=business_id, limit=100)}


def get_product_details(name: str, business_id: str = DEFAULT_BUSINESS_ID) -> Optional[Dict[str, Any]]:
    normalized = normalize_product_name(name)
    document = get_collection("products").find_one(
        {
            "business_id": business_id,
            "active": True,
            "$or": [
                {"name": normalized},
                {"display_name": {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}},
                {"tags": {"$elemMatch": {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}}},
            ],
        }
    )
    return _serialize_product(document) if document else None


def product_exists(name: str, business_id: str = DEFAULT_BUSINESS_ID) -> bool:
    return get_product_details(name, business_id=business_id) is not None


def add_product(
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    name: str,
    price: float,
    unit: str,
    category: Optional[str],
    description: str = "",
    tags: Optional[List[str]] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = normalize_product_name(name)
    now = utc_now()
    document = {
        "business_id": business_id,
        "name": normalized,
        "display_name": name.strip().title(),
        "price": float(price),
        "unit": unit.strip() or "unit",
        "category": (category or get_product_category(normalized, business_id)).strip().lower(),
        "description": description.strip(),
        "tags": [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()],
        "attributes": attributes or {},
        "active": True,
        "offer_price": None,
        "offer_until": None,
        "updated_at": now,
    }
    get_collection("products").update_one(
        {"business_id": business_id, "name": normalized},
        {"$set": document, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return get_product_details(normalized, business_id=business_id) or _serialize_product(document)


def update_product(
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    name: str,
    price: Optional[float] = None,
    unit: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    attributes: Optional[Dict[str, Any]] = None,
    active: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    normalized = normalize_product_name(name)
    update_fields: Dict[str, Any] = {"updated_at": utc_now()}
    if price is not None:
        update_fields["price"] = float(price)
    if unit is not None:
        update_fields["unit"] = unit.strip() or "unit"
    if category is not None:
        update_fields["category"] = category.strip().lower() or "other"
    if description is not None:
        update_fields["description"] = description.strip()
    if tags is not None:
        update_fields["tags"] = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    if attributes is not None:
        update_fields["attributes"] = attributes
    if active is not None:
        update_fields["active"] = bool(active)

    document = get_collection("products").find_one_and_update(
        {"business_id": business_id, "name": normalized},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize_product(document) if document else None


def delete_product(name: str, business_id: str = DEFAULT_BUSINESS_ID) -> bool:
    result = get_collection("products").update_one(
        {"business_id": business_id, "name": normalize_product_name(name)},
        {"$set": {"active": False, "updated_at": utc_now()}},
    )
    return result.matched_count > 0


def update_product_offer(product: str, offer_price: float, offer_until: Optional[str], business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    document = get_collection("products").find_one_and_update(
        {"business_id": business_id, "name": normalize_product_name(product)},
        {"$set": {"offer_price": float(offer_price), "offer_until": parse_datetime(offer_until), "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not document:
        raise RuntimeError(f"Product '{product}' not found.")
    return _serialize_product(document)


def clear_product_offer(product: str, business_id: str = DEFAULT_BUSINESS_ID) -> bool:
    result = get_collection("products").update_one(
        {"business_id": business_id, "name": normalize_product_name(product)},
        {"$set": {"offer_price": None, "offer_until": None, "updated_at": utc_now()}},
    )
    return result.matched_count > 0


def get_business_profile(business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    return get_business_by_id(business_id) or get_business_by_id(DEFAULT_BUSINESS_ID) or {
        "business_id": DEFAULT_BUSINESS_ID,
        "name": "Local Shop",
        "business_type": "retail",
        "description": "",
        "address": "",
        "hours": "",
        "contact_phone": "",
        "vendor_phone": "",
        "whatsapp_number": "",
        "support_details": "",
        "policies": "",
        "delivery_info": "",
        "broadcast_groups": [],
        "active": True,
    }


def list_orders(business_id: str = DEFAULT_BUSINESS_ID, *, limit: int = 20) -> List[Dict[str, Any]]:
    rows = get_collection("orders").find({"business_id": business_id}).sort("created_at", -1).limit(min(max(int(limit or 20), 1), 100))
    return [_serialize_order_summary(row) for row in rows]


def list_orders_filtered(
    business_id: str = DEFAULT_BUSINESS_ID,
    *,
    filter_by: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 20,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"business_id": business_id}
    normalized_filter = str(filter_by or "").strip().lower()
    now = utc_now()
    if normalized_filter == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query["created_at"] = {"$gte": start}
    elif normalized_filter in {"pending", "delivered"}:
        query["status"] = normalized_filter
    elif normalized_filter == "date_range":
        range_query: Dict[str, Any] = {}
        parsed_from = parse_datetime(date_from)
        parsed_to = parse_datetime(date_to)
        if parsed_from:
            range_query["$gte"] = parsed_from
        if parsed_to:
            range_query["$lte"] = parsed_to
        if range_query:
            query["created_at"] = range_query
    rows = get_collection("orders").find(query).sort("created_at", -1).limit(min(max(int(limit or 20), 1), 100))
    return [_serialize_order_summary(row) for row in rows]


def filter_orders(business_id: str = DEFAULT_BUSINESS_ID, *, status: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"business_id": business_id}
    if status:
        query["status"] = status
    rows = get_collection("orders").find(query).sort("created_at", -1).limit(min(max(int(limit or 20), 1), 100))
    return [_serialize_order_summary(row) for row in rows]


def summarize_sales(business_id: str = DEFAULT_BUSINESS_ID, *, time_window: str = "30d") -> Dict[str, Any]:
    orders = _orders_for_time_window(business_id, time_window=time_window)
    revenue = sum(float(row.get("total", 0.0)) for row in orders)
    unique_customers = {str(row.get("customer_phone") or "") for row in orders if row.get("customer_phone")}
    return {
        "time_window": time_window,
        "orders": len(orders),
        "revenue": round(revenue, 2),
        "avg_order_value": round(revenue / max(len(orders), 1), 2) if orders else 0.0,
        "unique_customers": len(unique_customers),
        "status_breakdown": _status_breakdown(orders),
    }


def get_top_products_for_window(business_id: str = DEFAULT_BUSINESS_ID, *, time_window: str = "30d", limit: int = 8) -> List[Dict[str, Any]]:
    orders = _orders_for_time_window(business_id, time_window=time_window)
    quantities: Dict[str, int] = {}
    revenue: Dict[str, float] = {}
    for row in orders:
        line_items = row.get("line_items") or row.get("items") or []
        for item in line_items:
            name = str(item.get("display_name") or item.get("name") or "").strip()
            if not name:
                continue
            qty = int(item.get("qty") or 1)
            quantities[name] = quantities.get(name, 0) + qty
            revenue[name] = revenue.get(name, 0.0) + float(item.get("line_total") or item.get("unit_price") or 0.0) * qty
    ranked = sorted(quantities.items(), key=lambda row: (row[1], revenue.get(row[0], 0.0)), reverse=True)
    return [
        {
            "name": name,
            "quantity": quantity,
            "revenue": round(revenue.get(name, 0.0), 2),
        }
        for name, quantity in ranked[: max(1, limit)]
    ]


def get_business_health_metrics(business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    summary = summarize_sales(business_id, time_window="30d")
    pending_orders = get_collection("orders").count_documents({"business_id": business_id, "status": "pending"})
    open_tickets = get_collection("support_tickets").count_documents({"business_id": business_id, "status": {"$ne": "resolved"}})
    automation_runs = list(get_collection("automation_runs").find({"business_id": business_id}).sort("created_at", -1).limit(100))
    successful_automation = sum(1 for row in automation_runs if row.get("converted"))
    return {
        "revenue_last_30d": summary["revenue"],
        "orders_last_30d": summary["orders"],
        "avg_order_value": summary["avg_order_value"],
        "pending_orders": pending_orders,
        "open_tickets": open_tickets,
        "automation_success_rate": round(successful_automation / max(len(automation_runs), 1), 4) if automation_runs else 0.0,
        "top_products": get_top_products_for_window(business_id, time_window="30d", limit=5),
    }


def bulk_update_orders(criteria: Dict[str, Any], action: Dict[str, Any], business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    status = str(action.get("status") or "").strip()
    if not status:
        raise ValueError("Bulk order update requires a target status.")
    rows = list_orders_filtered(
        business_id,
        filter_by=str(criteria.get("filter") or "date_range"),
        date_from=str(criteria.get("date_from") or ""),
        date_to=str(criteria.get("date_to") or ""),
        limit=min(max(int(criteria.get("limit") or 50), 1), 100),
    )
    updated: List[str] = []
    skipped: List[str] = []
    for row in rows:
        if criteria.get("current_status") and str(row.get("status") or "") != str(criteria.get("current_status") or ""):
            continue
        order = update_order_status(str(row.get("order_id") or ""), status, business_id=business_id)
        if order:
            updated.append(order.orderId)
        else:
            skipped.append(str(row.get("order_id") or ""))
    return {"updated": updated, "skipped": skipped, "target_status": status}


def summarize_business(business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    return {
        "business": get_business_profile(business_id),
        "order_count": get_collection("orders").count_documents({"business_id": business_id}),
        "pending_orders": get_collection("orders").count_documents({"business_id": business_id, "status": "pending"}),
        "product_count": get_collection("products").count_documents({"business_id": business_id, "active": True}),
        "support_open": get_collection("support_tickets").count_documents({"business_id": business_id, "status": {"$ne": "resolved"}}),
        "sales": summarize_sales(business_id, time_window="30d"),
        "health": get_business_health_metrics(business_id),
    }


def update_business_profile(
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    name: Optional[str] = None,
    business_type: Optional[str] = None,
    description: Optional[str] = None,
    address: Optional[str] = None,
    hours: Optional[str] = None,
    contact_phone: Optional[str] = None,
    support_details: Optional[str] = None,
    policies: Optional[str] = None,
    delivery_info: Optional[str] = None,
    broadcast_groups: Optional[List[str]] = None,
    messaging_rate_limit_per_minute: Optional[int] = None,
    automation_cooldown_minutes: Optional[int] = None,
    automation_max_messages_per_day: Optional[int] = None,
) -> Dict[str, Any]:
    update_fields: Dict[str, Any] = {"updated_at": utc_now()}
    if name is not None:
        update_fields["name"] = name.strip()
    if business_type is not None:
        update_fields["business_type"] = business_type.strip().lower()
    if description is not None:
        update_fields["description"] = description.strip()
    if address is not None:
        update_fields["address"] = address.strip()
    if hours is not None:
        update_fields["hours"] = hours.strip()
    if contact_phone is not None:
        update_fields["contact_phone"] = normalize_phone(contact_phone) if contact_phone.strip() else ""
    if support_details is not None:
        update_fields["support_details"] = support_details.strip()
    if policies is not None:
        update_fields["policies"] = policies.strip()
    if delivery_info is not None:
        update_fields["delivery_info"] = delivery_info.strip()
    if broadcast_groups is not None:
        update_fields["broadcast_groups"] = [normalize_phone(item) for item in broadcast_groups if str(item).strip()]
    if messaging_rate_limit_per_minute is not None:
        update_fields["messaging_rate_limit_per_minute"] = max(1, int(messaging_rate_limit_per_minute))
    if automation_cooldown_minutes is not None:
        update_fields["automation_cooldown_minutes"] = max(5, int(automation_cooldown_minutes))
    if automation_max_messages_per_day is not None:
        update_fields["automation_max_messages_per_day"] = max(1, int(automation_max_messages_per_day))

    get_collection("businesses").update_one({"business_id": business_id}, {"$set": update_fields}, upsert=False)
    return get_business_profile(business_id)


def create_support_ticket(
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    customer_phone: str,
    issue: str,
    order_id: Optional[str] = None,
    preferred_contact: Optional[str] = None,
) -> Dict[str, Any]:

    category = "general"

    issue_lower = issue.lower()

    if any(x in issue_lower for x in ["late", "delay"]):
        category = "delay"
    elif any(x in issue_lower for x in ["wrong", "incorrect"]):
        category = "wrong_item"
    elif any(x in issue_lower for x in ["refund", "money"]):
        category = "refund"

    priority = "normal"
    if category in ["wrong_item", "refund"]:
        priority = "high"

    document = {
        "business_id": business_id,
        "ticket_id": generate_id("support"),
        "customer_phone": customer_phone,
        "order_id": order_id,
        "issue": issue.strip(),
        "category": category,
        "priority": priority,
        "status": "open",
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }

    get_collection("support_tickets").insert_one(document)

    _notify_vendor_support_ticket(document, business_id=business_id)

    return {
        "ticket_id": document["ticket_id"],
        "category": category,
        "priority": priority,
        "status": document["status"],
    }


def create_promotion(
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    title: str,
    scope: str,
    target: Optional[str],
    discount_type: str,
    discount_value: float,
    starts_at: Optional[str] = None,
    ends_at: Optional[str] = None,
    broadcast_text: Optional[str] = None,
) -> Dict[str, Any]:
    document = {
        "business_id": business_id,
        "promotion_id": generate_id("promo"),
        "title": title.strip(),
        "scope": scope,
        "target": normalize_product_name(target) if target and scope == "product" else target,
        "discount_type": discount_type,
        "discount_value": float(discount_value),
        "starts_at": parse_datetime(starts_at) or utc_now(),
        "ends_at": parse_datetime(ends_at),
        "broadcast_text": (broadcast_text or "").strip() or None,
        "active": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    get_collection("promotions").insert_one(document)
    return serialize_promotion(document)


def create_event(
    *,
    business_id: str = DEFAULT_BUSINESS_ID,
    title: str,
    description: str,
    starts_at: Optional[str],
    ends_at: Optional[str] = None,
    promotion_scope: Optional[str] = None,
    promotion_target: Optional[str] = None,
    discount_type: Optional[str] = None,
    discount_value: Optional[float] = None,
    broadcast_text: Optional[str] = None,
) -> Dict[str, Any]:
    document = {
        "business_id": business_id,
        "event_id": generate_id("event"),
        "title": title.strip(),
        "description": description.strip(),
        "starts_at": parse_datetime(starts_at) or utc_now(),
        "ends_at": parse_datetime(ends_at),
        "promotion_scope": promotion_scope,
        "promotion_target": normalize_product_name(promotion_target) if promotion_scope == "product" and promotion_target else promotion_target,
        "discount_type": discount_type,
        "discount_value": float(discount_value) if discount_value is not None else None,
        "broadcast_text": (broadcast_text or "").strip() or None,
        "active": True,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    get_collection("events").insert_one(document)
    return serialize_event(document)


def get_active_promotions(business_id: str = DEFAULT_BUSINESS_ID) -> List[Dict[str, Any]]:
    current = utc_now()
    active: List[Dict[str, Any]] = []
    for doc in get_collection("promotions").find({"business_id": business_id, "active": True}):
        starts_at = parse_datetime(doc.get("starts_at")) or current
        ends_at = parse_datetime(doc.get("ends_at"))
        if starts_at <= current and (ends_at is None or ends_at >= current):
            active.append(serialize_promotion(doc))
    return active


def get_active_events(business_id: str = DEFAULT_BUSINESS_ID) -> List[Dict[str, Any]]:
    current = utc_now()
    active: List[Dict[str, Any]] = []
    for doc in get_collection("events").find({"business_id": business_id, "active": True}):
        starts_at = parse_datetime(doc.get("starts_at")) or current
        ends_at = parse_datetime(doc.get("ends_at"))
        if starts_at <= current and (ends_at is None or ends_at >= current):
            active.append(serialize_event(doc))
    return active


def get_pricing_adjustments(business_id: str = DEFAULT_BUSINESS_ID) -> List[Dict[str, Any]]:
    adjustments = get_active_promotions(business_id=business_id)
    for event in get_active_events(business_id=business_id):
        if event.get("discount_type") and event.get("discount_value") is not None:
            adjustments.append(
                {
                    "promotion_id": event["event_id"],
                    "title": event["title"],
                    "scope": event.get("promotion_scope") or "global",
                    "target": event.get("promotion_target"),
                    "discount_type": event["discount_type"],
                    "discount_value": event["discount_value"],
                    "source": "event",
                }
            )
    return adjustments


def send_vendor_order_notification(order: OrderRecord, business_id: str = DEFAULT_BUSINESS_ID) -> bool:
    vendor_phone = _vendor_contact_phone(business_id)
    if not vendor_phone:
        raise RuntimeError("Vendor phone is not configured for this business.")
    try:
        message_style = contact_style(vendor_phone, business_id)
    except Exception:
        message_style = "english"
    try:
        result = send_whatsapp_message(
            vendor_phone,
            build_vendor_order_message(order, style=message_style),
            from_number=get_business_profile(business_id).get("whatsapp_number"),
            business_id=business_id,
            notification_type="vendor_order_notification",
            metadata={"order_id": order.orderId},
        )
    except Exception as exc:
        result = {"success": False, "error": str(exc)}
    if result.get("success"):
        return True
    print("Vendor notification failed:", str(result.get("error") or "unknown"))
    print("=== VENDOR ORDER ===")
    print(order.model_dump_json(indent=2))
    print("====================")
    queue_vendor_notification(order, business_id=business_id, error=str(result.get("error") or "send failed"))
    return False


def build_vendor_order_message(order: OrderRecord, style: str = "english") -> str:
    return style_text(
        style,
        "New Order:\n"
        f"Order ID: {order.orderId}\n"
        f"Items: {', '.join(f'{item.name} x {item.qty}' for item in order.items)}\n"
        f"Address: {order.address}\n"
        f"Customer: {order.customerPhone}\n"
        f"Total: Rs {order.total:.2f}\n"
        "Reply naturally like 'accepted', 'dispatch cheythu', or 'delivered'.",
        "Puthiya order:\n"
        f"Order ID: {order.orderId}\n"
        f"Items: {', '.join(f'{item.name} x {item.qty}' for item in order.items)}\n"
        f"Address: {order.address}\n"
        f"Customer: {order.customerPhone}\n"
        f"Total: Rs {order.total:.2f}\n"
        "Natural ayi 'accepted', 'dispatch cheythu', 'delivered' pole reply cheyyu.",
        "പുതിയ ഓർഡർ:\n"
        f"Order ID: {order.orderId}\n"
        f"Items: {', '.join(f'{item.name} x {item.qty}' for item in order.items)}\n"
        f"Address: {order.address}\n"
        f"Customer: {order.customerPhone}\n"
        f"Total: Rs {order.total:.2f}\n"
        "‘accepted’, ‘dispatch cheythu’, ‘delivered’ പോലെ സ്വാഭാവികമായി മറുപടി നൽകുക.",
    )


def queue_vendor_notification(order: OrderRecord, business_id: str = DEFAULT_BUSINESS_ID, error: str = "") -> None:
    get_collection("vendor_notification_queue").update_one(
        {"business_id": business_id, "order_id": order.orderId},
        {
            "$set": {
                "business_id": business_id,
                "order_id": order.orderId,
                "payload_json": order.model_dump(),
                "status": "pending",
                "last_error": error or None,
                "updated_at": utc_now(),
            },
            "$setOnInsert": {"attempts": 0, "created_at": utc_now()},
        },
        upsert=True,
    )


def queue_vendor_dispatch(
    *,
    business_id: str,
    target_phone: str,
    payload: Dict[str, Any],
    notification_type: str,
    error: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    queue_key = {
        "business_id": business_id,
        "target_phone": target_phone,
        "notification_type": notification_type,
        "payload_hash": str(hash(json.dumps(payload, sort_keys=True, default=str))),
    }
    get_collection("vendor_notification_queue").update_one(
        queue_key,
        {
            "$set": {
                **queue_key,
                "payload_json": payload,
                "metadata": metadata or {},
                "status": "pending",
                "last_error": error or None,
                "updated_at": utc_now(),
            },
            "$setOnInsert": {"attempts": 0, "created_at": utc_now()},
        },
        upsert=True,
    )


def retry_failed_notifications(business_id: str = DEFAULT_BUSINESS_ID) -> int:
    sent = 0
    queue = get_collection("vendor_notification_queue")
    now = utc_now()
    for row in queue.find({"business_id": business_id, "status": "pending"}).sort("created_at", 1):
        next_attempt_at = parse_datetime(row.get("next_attempt_at"))
        if next_attempt_at and next_attempt_at > now:
            continue
        try:
            if row.get("order_id"):
                vendor_phone = _vendor_contact_phone(business_id)
                if not vendor_phone:
                    continue
                from_number = get_business_profile(business_id).get("whatsapp_number")
                order = OrderRecord.model_validate(row["payload_json"])
                result = send_whatsapp_message(
                    vendor_phone,
                    build_vendor_order_message(order, style=contact_style(vendor_phone, business_id)),
                    from_number=from_number,
                    business_id=business_id,
                    notification_type="vendor_order_notification",
                    metadata={"order_id": order.orderId, "idempotency_key": str(row.get("idempotency_key") or "")},
                )
            else:
                result = send_whatsapp_message(
                    str(row.get("target_phone") or ""),
                    str((row.get("payload_json") or {}).get("body") or ""),
                    from_number=str((row.get("payload_json") or {}).get("from_number") or ""),
                    business_id=business_id,
                    notification_type=str(row.get("notification_type") or "message"),
                    metadata=dict(row.get("metadata") or {}),
                )
            if not result.get("success"):
                if result.get("template_required") or result.get("status") == "rate_limited":
                    queue.update_one({"_id": row["_id"]}, {"$set": {"status": str(result.get("status") or "template_required"), "updated_at": utc_now()}})
                    continue
                raise RuntimeError(str(result.get("error") or "vendor send failed"))
            queue.update_one({"_id": row["_id"]}, {"$set": {"status": "sent", "updated_at": utc_now()}, "$inc": {"attempts": 1}})
            sent += 1
        except Exception as exc:
            attempts = int(row.get("attempts") or 0) + 1
            next_retry = utc_now() + timedelta(seconds=min(30 * (2 ** max(0, attempts - 1)), 1800))
            queue.update_one(
                {"_id": row["_id"]},
                {"$set": {"last_error": str(exc), "updated_at": utc_now(), "next_attempt_at": next_retry}, "$inc": {"attempts": 1}},
            )
    for row in retryable_dispatches(business_id):
        result = send_whatsapp_message(
            str(row.get("to") or ""),
            str(row.get("body") or ""),
            from_number=str(row.get("from") or ""),
            business_id=business_id,
            notification_type=str(row.get("notification_type") or "message"),
            metadata=dict(row.get("metadata") or {}) | {"idempotency_key": str(row.get("idempotency_key") or "")},
        )
        if result.get("success"):
            sent += 1
    return sent


def format_offer_preview(action: Dict[str, Any], style: str) -> str:
    date = action.get("date") or "open-ended"
    if style == "malayalam":
        return (
            f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\n"
            f"Until: {date}\nYES അയച്ചാൽ execute ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും."
        )
    if style == "manglish":
        return (
            f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\n"
            f"Until: {date}\nYES ayachal execute, NO ayachal cancel."
        )
    return (
        f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\n"
        f"Until: {date}\nReply YES to execute or NO to cancel."
    )


def notify_customer_order_update(customer_phone: str, order: OrderRecord, style_hint: str = "english", business_id: str = DEFAULT_BUSINESS_ID) -> None:
    if style_hint == "malayalam":
        message = f"നിങ്ങളുടെ order {order.orderId} ഇപ്പോള്‍ {order.status.replace('_', ' ')} ആണ്."
    elif style_hint == "manglish":
        message = f"Ningalude order {order.orderId} ippo {order.status.replace('_', ' ')} aanu."
    else:
        message = f"Your order {order.orderId} is now {order.status.replace('_', ' ')}."
    send_whatsapp_message(customer_phone, message, from_number=get_business_profile(business_id).get("whatsapp_number"), business_id=business_id, notification_type="customer_order_update", metadata={"order_id": order.orderId})


def generate_vendor_acknowledgement(order: OrderRecord, style: str, status: str) -> str:
    human_status = status.replace("_", " ")
    if style == "malayalam":
        return f"Order {order.orderId} {human_status} ആയി update ചെയ്തു."
    if style == "manglish":
        return f"Order {order.orderId} {human_status} ayi update cheythu."
    return f"Order {order.orderId} updated to {human_status}."


def get_customer_broadcast_targets(business_id: str = DEFAULT_BUSINESS_ID) -> List[str]:
    customers = set(get_collection("orders").distinct("customer_phone", {"business_id": business_id}))
    customers.update(get_collection("conversation_state").distinct("phone", {"business_id": business_id, "role": "customer"}))
    groups = set(get_business_profile(business_id).get("broadcast_groups", []))
    return sorted(phone for phone in (customers | groups) if phone)


def serialize_promotion(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "promotion_id": document["promotion_id"],
        "title": document["title"],
        "scope": document["scope"],
        "target": document.get("target"),
        "discount_type": document["discount_type"],
        "discount_value": float(document["discount_value"]),
        "starts_at": to_iso(parse_datetime(document.get("starts_at"))),
        "ends_at": to_iso(parse_datetime(document.get("ends_at"))),
        "broadcast_text": document.get("broadcast_text"),
    }


def serialize_event(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": document["event_id"],
        "title": document["title"],
        "description": document["description"],
        "starts_at": to_iso(parse_datetime(document.get("starts_at"))),
        "ends_at": to_iso(parse_datetime(document.get("ends_at"))),
        "promotion_scope": document.get("promotion_scope"),
        "promotion_target": document.get("promotion_target"),
        "discount_type": document.get("discount_type"),
        "discount_value": float(document["discount_value"]) if document.get("discount_value") is not None else None,
        "broadcast_text": document.get("broadcast_text"),
    }


def _serialize_product(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not document:
        return None
    return {
        "business_id": document.get("business_id", DEFAULT_BUSINESS_ID),
        "name": document["name"],
        "display_name": document.get("display_name", document["name"].title()),
        "price": float(document["price"]),
        "unit": document.get("unit", "unit"),
        "category": document.get("category", "other"),
        "description": document.get("description", ""),
        "tags": document.get("tags", []),
        "attributes": document.get("attributes", {}),
        "offer_price": float(document["offer_price"]) if document.get("offer_price") is not None else None,
        "offer_until": to_iso(parse_datetime(document.get("offer_until"))),
        "active": bool(document.get("active", True)),
    }


def _serialize_order_summary(document: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "order_id": document.get("order_id"),
        "customer_phone": document.get("customer_phone"),
        "status": document.get("status"),
        "total": float(document.get("total", 0.0)),
        "created_at": to_iso(parse_datetime(document.get("created_at"))),
        "updated_at": to_iso(parse_datetime(document.get("updated_at"))),
    }


def _serialize_business(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not document:
        return None
    return {
        "business_id": document["business_id"],
        "slug": document.get("slug", slugify(document.get("name", "business"))),
        "name": document.get("name", "Business"),
        "business_type": document.get("business_type", "retail"),
        "description": document.get("description", ""),
        "address": document.get("address", ""),
        "hours": document.get("hours", ""),
        "contact_phone": document.get("contact_phone", ""),
        "vendor_phone": document.get("vendor_phone", ""),
        "whatsapp_number": document.get("whatsapp_number", ""),
        "support_details": document.get("support_details", ""),
        "policies": document.get("policies", ""),
        "delivery_info": document.get("delivery_info", ""),
        "broadcast_groups": document.get("broadcast_groups", []),
        "messaging_rate_limit_per_minute": int(document.get("messaging_rate_limit_per_minute", 30)),
        "automation_cooldown_minutes": int(document.get("automation_cooldown_minutes", 180)),
        "automation_max_messages_per_day": int(document.get("automation_max_messages_per_day", 2)),
        "active": bool(document.get("active", True)),
        "created_at": to_iso(parse_datetime(document.get("created_at"))),
        "updated_at": to_iso(parse_datetime(document.get("updated_at"))),
    }


def _status_breakdown(orders: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in orders:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _orders_for_time_window(business_id: str, *, time_window: str) -> List[Dict[str, Any]]:
    normalized = str(time_window or "30d").strip().lower()
    days = 30
    if normalized.endswith("d") and normalized[:-1].isdigit():
        days = max(1, int(normalized[:-1]))
    elif normalized.endswith("w") and normalized[:-1].isdigit():
        days = max(1, int(normalized[:-1]) * 7)
    elif normalized.endswith("m") and normalized[:-1].isdigit():
        days = max(1, int(normalized[:-1]) * 30)
    cutoff = utc_now() - timedelta(days=days)
    return list(get_collection("orders").find({"business_id": business_id, "created_at": {"$gte": cutoff}}))


def _find_reusable_business(vendor_phone: str, whatsapp_number: str, slug: str) -> Optional[Dict[str, Any]]:
    candidates = []
    if vendor_phone:
        candidates.append({"vendor_phone": vendor_phone})
    if whatsapp_number:
        candidates.append({"whatsapp_number": whatsapp_number})
    if slug:
        candidates.append({"slug": slug})
    if not candidates:
        return None
    return get_collection("businesses").find_one({"active": False, "$or": candidates})


def _ensure_business_conflicts_clear(
    vendor_phone: str,
    whatsapp_number: str,
    slug: str,
    exclude_business_id: str = "",
) -> None:
    conflicts = []
    if vendor_phone:
        conflicts.append(("vendor_phone", vendor_phone))
    if whatsapp_number:
        conflicts.append(("whatsapp_number", whatsapp_number))
    if slug:
        conflicts.append(("slug", slug))
    for field, value in conflicts:
        query: Dict[str, Any] = {field: value, "active": True}
        if exclude_business_id:
            query["business_id"] = {"$ne": exclude_business_id}
        existing = get_collection("businesses").find_one(query)
        if existing:
            raise ValueError(f"An active business already uses this {field.replace('_', ' ')}.")


def _release_inactive_business_conflicts(
    *,
    current_business_id: str = "",
    vendor_phone: str,
    whatsapp_number: str,
    slug: str,
) -> None:
    candidates = []
    if vendor_phone:
        candidates.append({"vendor_phone": vendor_phone})
    if whatsapp_number:
        candidates.append({"whatsapp_number": whatsapp_number})
    if slug:
        candidates.append({"slug": slug})
    if not candidates:
        return

    query: Dict[str, Any] = {"active": False, "$or": candidates}
    if current_business_id:
        query["business_id"] = {"$ne": current_business_id}

    for business in get_collection("businesses").find(query):
        _archive_deleted_business_identity(str(business.get("business_id") or ""))


def _archive_deleted_business_identity(business_id: str) -> None:
    if not business_id:
        return
    now = utc_now()
    archive_suffix = f"archived-{business_id}"
    get_collection("businesses").update_one(
        {"business_id": business_id, "active": False},
        {
            "$set": {
                "vendor_phone": f"archived:{archive_suffix}",
                "whatsapp_number": f"archived:{archive_suffix}",
                "slug": archive_suffix,
                "updated_at": now,
            }
        },
    )


def _vendor_contact_phone(business_id: str) -> str:
    profile = get_business_profile(business_id)
    selected = str(profile.get("contact_phone") or profile.get("vendor_phone") or "").strip()
    return normalize_phone(selected) if selected else ""


def _notify_vendor_support_ticket(ticket: Dict[str, Any], business_id: str = DEFAULT_BUSINESS_ID) -> None:
    vendor_phone = _vendor_contact_phone(business_id)
    if not vendor_phone:
        return
    message = (
        "Customer Support Ticket:\n"
        f"Ticket ID: {ticket['ticket_id']}\n"
        f"Customer: {ticket['customer_phone']}\n"
        f"Issue: {ticket['issue']}\n"
        f"Order ID: {ticket.get('order_id') or 'N/A'}"
    )
    try:
        send_whatsapp_message(vendor_phone, message, from_number=get_business_profile(business_id).get("whatsapp_number"))
    except Exception as exc:
        print("Support ticket notification failed:", str(exc))
        print(message)


def format_offer_preview_legacy_removed(action: Dict[str, Any], style: str) -> str:
    date = action.get("date") or "open-ended"
    return style_text(
        style,
        f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\nUntil: {date}\nReply YES to execute or NO to cancel.",
        f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\nUntil: {date}\nYES ayachal execute, NO ayachal cancel.",
        f"ഓഫർ പ്രിവ്യൂ:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\nUntil: {date}\nYES അയച്ചാൽ execute ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും.",
    )


def notify_customer_order_update_legacy_removed(customer_phone: str, order: OrderRecord, style_hint: str = "english", business_id: str = DEFAULT_BUSINESS_ID) -> None:
    message = style_text(
        style_hint,
        f"Your order {order.orderId} is now {order.status.replace('_', ' ')}.",
        f"Ningalude order {order.orderId} ippo {status_phrase(order.status, 'manglish')} aanu.",
        f"നിങ്ങളുടെ order {order.orderId} ഇപ്പോൾ {status_phrase(order.status, 'malayalam')} ആണ്.",
    )
    try:
        send_whatsapp_message(customer_phone, message, from_number=get_business_profile(business_id).get("whatsapp_number"))
    except Exception as exc:
        _log_vendor("customer_notification_failed", customer_phone=customer_phone, order_id=order.orderId, error=str(exc))


def generate_vendor_acknowledgement_legacy_removed(order: OrderRecord, style: str, status: str) -> str:
    human_status = status.replace("_", " ")
    return style_text(
        style,
        f"Order {order.orderId} updated to {human_status}.",
        f"Order {order.orderId} {human_status} ayi update cheythu.",
        f"Order {order.orderId} {status_phrase(status, 'malayalam')} ആയി update ചെയ്തു.",
    )


def _notify_vendor_support_ticket_legacy_removed(ticket: Dict[str, Any], business_id: str = DEFAULT_BUSINESS_ID) -> None:
    vendor_phone = _vendor_contact_phone(business_id)
    if not vendor_phone:
        return
    style = contact_style(vendor_phone, business_id)
    message = style_text(
        style,
        "Customer Support Ticket:\n"
        f"Ticket ID: {ticket['ticket_id']}\n"
        f"Customer: {ticket['customer_phone']}\n"
        f"Issue: {ticket['issue']}\n"
        f"Order ID: {ticket.get('order_id') or 'N/A'}",
        "Customer support ticket:\n"
        f"Ticket ID: {ticket['ticket_id']}\n"
        f"Customer: {ticket['customer_phone']}\n"
        f"Issue: {ticket['issue']}\n"
        f"Order ID: {ticket.get('order_id') or 'N/A'}",
        "കസ്റ്റമർ സപ്പോർട്ട് ടിക്കറ്റ്:\n"
        f"Ticket ID: {ticket['ticket_id']}\n"
        f"Customer: {ticket['customer_phone']}\n"
        f"Issue: {ticket['issue']}\n"
        f"Order ID: {ticket.get('order_id') or 'N/A'}",
    )
    try:
        send_whatsapp_message(vendor_phone, message, from_number=get_business_profile(business_id).get("whatsapp_number"), business_id=business_id, notification_type="support_ticket_notification", metadata={"ticket_id": ticket.get("ticket_id")})
    except Exception as exc:
        print("Support ticket notification failed:", str(exc))
        print(message)
