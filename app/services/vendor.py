import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from app.db.mongo import generate_id, get_collection, parse_datetime, to_iso, utc_now
from app.models.order import OrderRecord


def normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if phone.startswith("whatsapp:"):
        return phone
    if phone.startswith("+"):
        return f"whatsapp:{phone}"
    digits = re.sub(r"[^\d+]", "", phone)
    if not digits.startswith("+"):
        digits = f"+{digits}"
    return f"whatsapp:{digits}"


def send_whatsapp_message(to: str, body: str) -> Dict[str, Any]:
    if os.getenv("DEBUG", "").strip().lower() == "true":
        print("=== DEBUG WHATSAPP MESSAGE ===")
        print(f"TO: {normalize_phone(to)}")
        print(body)
        print("==============================")
        return {"ok": True, "debug": True}

    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip()
    if not all([account_sid, auth_token, from_number]):
        raise RuntimeError("Twilio WhatsApp environment variables are not fully configured.")

    payload = urllib.parse.urlencode(
        {
            "From": normalize_phone(from_number),
            "To": normalize_phone(to),
            "Body": body,
        }
    ).encode("utf-8")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
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


def get_product_category(name: str) -> str:
    product = get_product_details(name)
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
    filter_category: Optional[str] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    tags: Optional[List[str]] = None,
    sort_by: str = "display_name",
    sort_order: str = "asc",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    collection = get_collection("products")
    query: Dict[str, Any] = {"active": True}
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

    products = []
    allowed_sort_fields = {"display_name", "price", "category", "updated_at"}
    mongo_sort_field = sort_by if sort_by in allowed_sort_fields else "display_name"
    mongo_sort_order = 1 if str(sort_order).lower() != "desc" else -1
    safe_limit = min(max(int(limit or 10), 1), 20)
    for doc in collection.find(query).sort(mongo_sort_field, mongo_sort_order).limit(safe_limit):
        products.append(_serialize_product(doc))
    return products


def get_product_catalog_map() -> Dict[str, Dict[str, Any]]:
    return {product["name"]: product for product in get_product_catalog()}


def get_product_details(name: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_product_name(name)
    collection = get_collection("products")
    document = collection.find_one(
        {
            "active": True,
            "$or": [
                {"name": normalized},
                {"display_name": {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}},
                {"tags": {"$elemMatch": {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"}}},
            ],
        }
    )
    return _serialize_product(document) if document else None


def product_exists(name: str) -> bool:
    return get_product_details(name) is not None


def add_product(
    *,
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
        "name": normalized,
        "display_name": name.strip().title(),
        "price": float(price),
        "unit": unit.strip() or "unit",
        "category": (category or get_product_category(normalized)).strip().lower(),
        "description": description.strip(),
        "tags": [str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()],
        "attributes": attributes or {},
        "active": True,
        "offer_price": None,
        "offer_until": None,
        "created_at": now,
        "updated_at": now,
    }
    get_collection("products").update_one({"name": normalized}, {"$set": document}, upsert=True)
    return get_product_details(normalized) or _serialize_product(document)


def update_product(
    *,
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
        {"name": normalized},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize_product(document) if document else None


def delete_product(name: str) -> bool:
    normalized = normalize_product_name(name)
    result = get_collection("products").update_one(
        {"name": normalized},
        {"$set": {"active": False, "updated_at": utc_now()}},
    )
    return result.matched_count > 0


def update_product_offer(product: str, offer_price: float, offer_until: Optional[str]) -> Dict[str, Any]:
    normalized = normalize_product_name(product)
    document = get_collection("products").find_one_and_update(
        {"name": normalized},
        {
            "$set": {
                "offer_price": float(offer_price),
                "offer_until": parse_datetime(offer_until),
                "updated_at": utc_now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not document:
        raise RuntimeError(f"Product '{normalized}' not found.")
    return _serialize_product(document)


def clear_product_offer(product: str) -> bool:
    normalized = normalize_product_name(product)
    result = get_collection("products").update_one(
        {"name": normalized},
        {"$set": {"offer_price": None, "offer_until": None, "updated_at": utc_now()}},
    )
    return result.matched_count > 0


def get_business_profile() -> Dict[str, Any]:
    document = get_collection("business_profile").find_one({"profile_id": "default"}) or {}
    return {
        "name": document.get("name", "Local Shop"),
        "business_type": document.get("business_type", "retail"),
        "description": document.get("description", ""),
        "address": document.get("address", ""),
        "hours": document.get("hours", ""),
        "contact_phone": document.get("contact_phone", ""),
        "support_details": document.get("support_details", ""),
        "policies": document.get("policies", ""),
        "delivery_info": document.get("delivery_info", ""),
        "broadcast_groups": document.get("broadcast_groups", []),
    }


def update_business_profile(
    *,
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
        update_fields["contact_phone"] = contact_phone.strip()
    if support_details is not None:
        update_fields["support_details"] = support_details.strip()
    if policies is not None:
        update_fields["policies"] = policies.strip()
    if delivery_info is not None:
        update_fields["delivery_info"] = delivery_info.strip()
    if broadcast_groups is not None:
        update_fields["broadcast_groups"] = [normalize_phone(item) for item in broadcast_groups if str(item).strip()]

    get_collection("business_profile").update_one(
        {"profile_id": "default"},
        {
            "$set": update_fields,
            "$setOnInsert": {"profile_id": "default", "created_at": utc_now()},
        },
        upsert=True,
    )
    return get_business_profile()


def create_support_ticket(
    *,
    customer_phone: str,
    issue: str,
    order_id: Optional[str] = None,
    preferred_contact: Optional[str] = None,
) -> Dict[str, Any]:
    document = {
        "ticket_id": generate_id("support"),
        "customer_phone": customer_phone,
        "order_id": order_id,
        "issue": issue.strip(),
        "preferred_contact": preferred_contact.strip() if preferred_contact else None,
        "status": "open",
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    get_collection("support_tickets").insert_one(document)
    _notify_vendor_support_ticket(document)
    return {
        "ticket_id": document["ticket_id"],
        "issue": document["issue"],
        "status": document["status"],
    }


def create_promotion(
    *,
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


def get_active_promotions() -> List[Dict[str, Any]]:
    current = utc_now()
    active: List[Dict[str, Any]] = []
    for doc in get_collection("promotions").find({"active": True}):
        starts_at = parse_datetime(doc.get("starts_at")) or current
        ends_at = parse_datetime(doc.get("ends_at"))
        if starts_at <= current and (ends_at is None or ends_at >= current):
            active.append(serialize_promotion(doc))
    return active


def get_active_events() -> List[Dict[str, Any]]:
    current = utc_now()
    active: List[Dict[str, Any]] = []
    for doc in get_collection("events").find({"active": True}):
        starts_at = parse_datetime(doc.get("starts_at")) or current
        ends_at = parse_datetime(doc.get("ends_at"))
        if starts_at <= current and (ends_at is None or ends_at >= current):
            active.append(serialize_event(doc))
    return active


def get_pricing_adjustments() -> List[Dict[str, Any]]:
    adjustments = get_active_promotions()
    for event in get_active_events():
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


def send_vendor_order_notification(order: OrderRecord) -> bool:
    vendor_phone = _vendor_contact_phone()
    if not vendor_phone:
        raise RuntimeError("VENDOR_PHONE or business contact phone is not configured.")

    message = build_vendor_order_message(order)
    try:
        send_whatsapp_message(vendor_phone, message)
        return True
    except Exception as exc:
        print("Vendor notification failed:", str(exc))
        print("=== VENDOR ORDER ===")
        print(order.model_dump_json(indent=2))
        print("====================")
        queue_vendor_notification(order, error=str(exc))
        return False


def build_vendor_order_message(order: OrderRecord) -> str:
    return (
        "New Order:\n"
        f"Order ID: {order.orderId}\n"
        f"Items: {', '.join(f'{item.name} x {item.qty}' for item in order.items)}\n"
        f"Address: {order.address}\n"
        f"Customer: {order.customerPhone}\n"
        f"Total: Rs {order.total:.2f}\n"
        "Reply in natural language like 'accepted', 'dispatch cheythu', or 'delivered'."
    )


def queue_vendor_notification(order: OrderRecord, error: str = "") -> None:
    get_collection("vendor_notification_queue").update_one(
        {"order_id": order.orderId},
        {
            "$set": {
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


def retry_failed_notifications() -> int:
    sent = 0
    vendor_phone = _vendor_contact_phone()
    if not vendor_phone:
        return sent

    queue = get_collection("vendor_notification_queue")
    for row in queue.find({"status": "pending"}).sort("created_at", 1):
        order = OrderRecord.model_validate(row["payload_json"])
        message = build_vendor_order_message(order)
        try:
            send_whatsapp_message(vendor_phone, message)
            queue.update_one(
                {"_id": row["_id"]},
                {"$set": {"status": "sent", "updated_at": utc_now()}, "$inc": {"attempts": 1}},
            )
            sent += 1
        except Exception as exc:
            print("Vendor notification retry failed:", str(exc))
            print("=== VENDOR ORDER ===")
            print(order.model_dump_json(indent=2))
            print("====================")
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


def notify_customer_order_update(customer_phone: str, order: OrderRecord, style_hint: str = "english") -> None:
    if style_hint == "malayalam":
        message = f"നിങ്ങളുടെ order {order.orderId} ഇപ്പോൾ {order.status.replace('_', ' ')} ആണ്."
    elif style_hint == "manglish":
        message = f"Ningalude order {order.orderId} ippo {order.status.replace('_', ' ')} aanu."
    else:
        message = f"Your order {order.orderId} is now {order.status.replace('_', ' ')}."
    send_whatsapp_message(customer_phone, message)


def generate_vendor_acknowledgement(order: OrderRecord, style: str, status: str) -> str:
    human_status = status.replace("_", " ")
    if style == "malayalam":
        return f"Order {order.orderId} {human_status} ആയി update ചെയ്തു."
    if style == "manglish":
        return f"Order {order.orderId} {human_status} ayi update cheythu."
    return f"Order {order.orderId} updated to {human_status}."


def get_customer_broadcast_targets() -> List[str]:
    customers = set(get_collection("orders").distinct("customer_phone"))
    customers.update(get_collection("conversation_state").distinct("phone", {"role": "customer"}))
    groups = set(get_business_profile().get("broadcast_groups", []))
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
    }


def _vendor_contact_phone() -> str:
    profile_phone = str(get_business_profile().get("contact_phone") or "").strip()
    env_phone = os.getenv("VENDOR_PHONE", "").strip()
    selected = profile_phone or env_phone
    return normalize_phone(selected) if selected else ""


def _notify_vendor_support_ticket(ticket: Dict[str, Any]) -> None:
    vendor_phone = _vendor_contact_phone()
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
        send_whatsapp_message(vendor_phone, message)
    except Exception as exc:
        print("Support ticket notification failed:", str(exc))
        print(message)
