import uuid
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from app.db.mongo import get_collection, utc_now
from app.models.order import OrderItem, OrderRecord
from app.services.vendor import get_pricing_adjustments, get_product_catalog_map, normalize_product_name


def create_order(customer_phone: str, items: List[Dict[str, Any]], address: str) -> OrderRecord:
    pricing = calculate_order_pricing(items)
    now = utc_now()
    order = OrderRecord(
        orderId=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        customerPhone=customer_phone,
        items=[OrderItem(name=item["name"], qty=item["qty"]) for item in pricing["items"]],
        total=round(pricing["total"], 2),
        status="pending",
        address=address,
        createdAt=now.isoformat(),
    )
    get_collection("orders").insert_one(
        {
            "order_id": order.orderId,
            "customer_phone": order.customerPhone,
            "items": [item.model_dump() for item in order.items],
            "line_items": pricing["line_items"],
            "total": order.total,
            "status": order.status,
            "address": order.address,
            "created_at": now,
            "updated_at": now,
        }
    )
    return order


def update_order_status(order_id: str, status: str) -> Optional[OrderRecord]:
    document = get_collection("orders").find_one_and_update(
        {"order_id": order_id},
        {"$set": {"status": status, "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    return _doc_to_order(document) if document else None


def get_order_by_id(order_id: str) -> Optional[OrderRecord]:
    document = get_collection("orders").find_one({"order_id": order_id})
    return _doc_to_order(document) if document else None


def get_latest_order(customer_phone: str) -> Optional[OrderRecord]:
    document = get_collection("orders").find_one({"customer_phone": customer_phone}, sort=[("created_at", -1)])
    return _doc_to_order(document) if document else None


def get_latest_vendor_actionable_order(
    target_order_id: Optional[str] = None,
    desired_status: Optional[str] = None,
) -> Optional[OrderRecord]:
    collection = get_collection("orders")
    if target_order_id:
        return get_order_by_id(target_order_id)

    status_filter: Dict[str, Any] = {}
    if desired_status in {"accepted", "rejected"}:
        status_filter["status"] = "pending"
    elif desired_status == "out_for_delivery":
        status_filter["status"] = "accepted"
    elif desired_status == "delivered":
        status_filter["status"] = "out_for_delivery"

    document = collection.find_one(status_filter, sort=[("created_at", -1)])
    return _doc_to_order(document) if document else None


def calculate_order_pricing(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    catalog = get_product_catalog_map()
    adjustments = get_pricing_adjustments()
    normalized_items: List[Dict[str, Any]] = []
    line_items: List[Dict[str, Any]] = []
    total = 0.0

    for item in items:
        name = normalize_product_name(str(item.get("name", "")).strip())
        qty = int(item.get("qty", 1))
        product = catalog.get(name)
        if not product or qty <= 0:
            continue

        base_price = float(product["price"])
        offer_price = float(product["offer_price"]) if product.get("offer_price") is not None else base_price
        unit_price = min(base_price, offer_price)
        applied = choose_best_adjustment(product, unit_price, adjustments)
        final_unit_price = applied["price"] if applied else unit_price
        line_total = round(final_unit_price * qty, 2)

        normalized_items.append({"name": name, "qty": qty})
        line_items.append(
            {
                "name": name,
                "display_name": product.get("display_name", name.title()),
                "qty": qty,
                "unit_price": round(final_unit_price, 2),
                "line_total": line_total,
                "promotion": applied["title"] if applied else None,
            }
        )
        total += line_total

    return {"items": normalized_items, "line_items": line_items, "total": round(total, 2)}


def choose_best_adjustment(product: Dict[str, Any], unit_price: float, adjustments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    for adjustment in adjustments:
        if not adjustment_applies(product, adjustment):
            continue

        discount_type = adjustment.get("discount_type")
        discount_value = float(adjustment.get("discount_value") or 0)
        discounted = unit_price
        if discount_type == "percent":
            discounted = max(0.0, unit_price * (1 - (discount_value / 100.0)))
        elif discount_type == "flat":
            discounted = max(0.0, unit_price - discount_value)

        if best is None or discounted < best["price"]:
            best = {"title": adjustment.get("title") or "Promotion", "price": round(discounted, 2)}
    return best


def adjustment_applies(product: Dict[str, Any], adjustment: Dict[str, Any]) -> bool:
    scope = adjustment.get("scope") or "global"
    target = adjustment.get("target")
    if scope == "global":
        return True
    if scope == "category":
        return target == product.get("category")
    if scope == "product":
        return target == product.get("name")
    return False


def build_order_preview(items: List[Dict[str, Any]], address: str, style: str = "english") -> Dict[str, str]:
    pricing = calculate_order_pricing(items)
    price_lines = []
    for line in pricing["line_items"]:
        detail = f"- {line['display_name']} x {line['qty']} = Rs {line['line_total']:.2f}"
        if line.get("promotion"):
            detail += f" ({line['promotion']})"
        price_lines.append(detail)

    if style == "malayalam":
        suffix = "YES അയച്ചാൽ confirm ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും."
    elif style == "manglish":
        suffix = "YES ayachal confirm, NO ayachal cancel."
    else:
        suffix = "Reply YES to confirm or NO to cancel."

    message = (
        "Order preview:\n"
        + "\n".join(price_lines)
        + f"\nAddress: {address}\nEstimated total: Rs {pricing['total']:.2f}\n{suffix}"
    )
    return {"message": message, "total": f"{pricing['total']:.2f}"}


def format_order_summary(items: List[OrderItem]) -> str:
    return ", ".join(f"{item.name} x {item.qty}" for item in items)


def _doc_to_order(document: Dict[str, Any]) -> OrderRecord:
    return OrderRecord(
        orderId=document["order_id"],
        customerPhone=document["customer_phone"],
        items=[OrderItem(**item) for item in document.get("items", [])],
        total=float(document["total"]),
        status=document["status"],
        address=document["address"],
        createdAt=document["created_at"].isoformat() if hasattr(document.get("created_at"), "isoformat") else str(document["created_at"]),
    )
