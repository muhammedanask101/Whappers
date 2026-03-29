import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from app.db.mongo import DEFAULT_BUSINESS_ID, generate_id, get_collection, parse_datetime, slugify, to_iso, utc_now
from app.services.agent.localization import contact_style, status_phrase, style_text
from app.models.order import OrderRecord


def normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if not phone:
        return ""
    if phone.startswith("whatsapp:"):
        return phone
    if phone.startswith("+"):
        return f"whatsapp:{phone}"
    digits = re.sub(r"[^\d+]", "", phone)
    if not digits:
        return ""
    if not digits.startswith("+"):
        digits = f"+{digits}"
    return f"whatsapp:{digits}"


def send_whatsapp_message(to: str, body: str, from_number: Optional[str] = None) -> Dict[str, Any]:
    normalized_to = normalize_phone(to)
    normalized_from = normalize_phone(from_number or os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip())
    if os.getenv("DEBUG", "").strip().lower() == "true":
        print("=== DEBUG WHATSAPP MESSAGE ===")
        print(f"FROM: {normalized_from}")
        print(f"TO: {normalized_to}")
        print(body)
        print("==============================")
        return {"ok": True, "debug": True}

    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not all([account_sid, auth_token, normalized_from]):
        raise RuntimeError("Twilio WhatsApp environment variables are not fully configured.")

    payload = urllib.parse.urlencode({"From": normalized_from, "To": normalized_to, "Body": body}).encode("utf-8")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Twilio message send failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Twilio message send failed: {exc.reason}") from exc


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
        send_whatsapp_message(vendor_phone, build_vendor_order_message(order, style=contact_style(vendor_phone, business_id)), from_number=get_business_profile(business_id).get("whatsapp_number"))
        return True
    except Exception as exc:
        print("Vendor notification failed:", str(exc))
        print("=== VENDOR ORDER ===")
        print(order.model_dump_json(indent=2))
        print("====================")
        queue_vendor_notification(order, business_id=business_id, error=str(exc))
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


def retry_failed_notifications(business_id: str = DEFAULT_BUSINESS_ID) -> int:
    sent = 0
    vendor_phone = _vendor_contact_phone(business_id)
    if not vendor_phone:
        return sent

    queue = get_collection("vendor_notification_queue")
    from_number = get_business_profile(business_id).get("whatsapp_number")
    for row in queue.find({"business_id": business_id, "status": "pending"}).sort("created_at", 1):
        order = OrderRecord.model_validate(row["payload_json"])
        try:
            send_whatsapp_message(vendor_phone, build_vendor_order_message(order, style=contact_style(vendor_phone, business_id)), from_number=from_number)
            queue.update_one({"_id": row["_id"]}, {"$set": {"status": "sent", "updated_at": utc_now()}, "$inc": {"attempts": 1}})
            sent += 1
        except Exception as exc:
            queue.update_one(
                {"_id": row["_id"]},
                {"$set": {"last_error": str(exc), "updated_at": utc_now()}, "$inc": {"attempts": 1}},
            )
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
    send_whatsapp_message(customer_phone, message, from_number=get_business_profile(business_id).get("whatsapp_number"))


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
        "active": bool(document.get("active", True)),
        "created_at": to_iso(parse_datetime(document.get("created_at"))),
        "updated_at": to_iso(parse_datetime(document.get("updated_at"))),
    }


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


def format_offer_preview(action: Dict[str, Any], style: str) -> str:
    date = action.get("date") or "open-ended"
    return style_text(
        style,
        f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\nUntil: {date}\nReply YES to execute or NO to cancel.",
        f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\nUntil: {date}\nYES ayachal execute, NO ayachal cancel.",
        f"ഓഫർ പ്രിവ്യൂ:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\nUntil: {date}\nYES അയച്ചാൽ execute ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും.",
    )


def notify_customer_order_update(customer_phone: str, order: OrderRecord, style_hint: str = "english", business_id: str = DEFAULT_BUSINESS_ID) -> None:
    message = style_text(
        style_hint,
        f"Your order {order.orderId} is now {order.status.replace('_', ' ')}.",
        f"Ningalude order {order.orderId} ippo {status_phrase(order.status, 'manglish')} aanu.",
        f"നിങ്ങളുടെ order {order.orderId} ഇപ്പോൾ {status_phrase(order.status, 'malayalam')} ആണ്.",
    )
    send_whatsapp_message(customer_phone, message, from_number=get_business_profile(business_id).get("whatsapp_number"))


def generate_vendor_acknowledgement(order: OrderRecord, style: str, status: str) -> str:
    human_status = status.replace("_", " ")
    return style_text(
        style,
        f"Order {order.orderId} updated to {human_status}.",
        f"Order {order.orderId} {human_status} ayi update cheythu.",
        f"Order {order.orderId} {status_phrase(status, 'malayalam')} ആയി update ചെയ്തു.",
    )


def _notify_vendor_support_ticket(ticket: Dict[str, Any], business_id: str = DEFAULT_BUSINESS_ID) -> None:
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
        send_whatsapp_message(vendor_phone, message, from_number=get_business_profile(business_id).get("whatsapp_number"))
    except Exception as exc:
        print("Support ticket notification failed:", str(exc))
        print(message)
