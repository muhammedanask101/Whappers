import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.database import execute, fetch_one
from app.models.order import OrderItem, OrderRecord
from app.services.vendor import get_product_catalog_map


def create_order(customer_phone: str, items: List[Dict[str, Any]], address: str) -> OrderRecord:
    catalog = get_product_catalog_map()
    normalized_items: List[OrderItem] = []
    total = 0.0
    for item in items:
        name = item["name"].strip().lower()
        qty = int(item["qty"])
        normalized_items.append(OrderItem(name=name, qty=qty))
        pricing = catalog.get(name)
        if pricing:
            effective_price = pricing["offer_price"] if pricing["offer_price"] is not None else pricing["price"]
            total += effective_price * qty

    now = datetime.now(timezone.utc).isoformat()
    order = OrderRecord(
        orderId=f"ORD-{uuid.uuid4().hex[:8].upper()}",
        customerPhone=customer_phone,
        items=normalized_items,
        total=round(total, 2),
        status="pending",
        address=address,
        createdAt=now,
    )
    execute(
        """
        INSERT INTO orders (order_id, customer_phone, items_json, total, status, address, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order.orderId,
            order.customerPhone,
            json.dumps([item.model_dump() for item in order.items]),
            order.total,
            order.status,
            order.address,
            order.createdAt,
            now,
        ),
    )
    return order


def update_order_status(order_id: str, status: str) -> Optional[OrderRecord]:
    now = datetime.now(timezone.utc).isoformat()
    execute("UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?", (status, now, order_id))
    return get_order_by_id(order_id)


def get_order_by_id(order_id: str) -> Optional[OrderRecord]:
    row = fetch_one("SELECT * FROM orders WHERE order_id = ?", (order_id,))
    return _row_to_order(row) if row else None


def get_latest_order(customer_phone: str) -> Optional[OrderRecord]:
    row = fetch_one(
        "SELECT * FROM orders WHERE customer_phone = ? ORDER BY created_at DESC LIMIT 1",
        (customer_phone,),
    )
    return _row_to_order(row) if row else None


def get_latest_vendor_actionable_order(
    target_order_id: Optional[str] = None,
    desired_status: Optional[str] = None,
) -> Optional[OrderRecord]:
    if target_order_id:
        return get_order_by_id(target_order_id)

    if desired_status in {"accepted", "rejected"}:
        row = fetch_one("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at DESC LIMIT 1")
        return _row_to_order(row) if row else None
    if desired_status == "out_for_delivery":
        row = fetch_one("SELECT * FROM orders WHERE status = 'accepted' ORDER BY created_at DESC LIMIT 1")
        return _row_to_order(row) if row else None
    if desired_status == "delivered":
        row = fetch_one("SELECT * FROM orders WHERE status = 'out_for_delivery' ORDER BY created_at DESC LIMIT 1")
        return _row_to_order(row) if row else None

    row = fetch_one("SELECT * FROM orders ORDER BY created_at DESC LIMIT 1")
    return _row_to_order(row) if row else None


def build_order_preview(items: List[Dict[str, Any]], address: str, style: str = "english") -> Dict[str, str]:
    catalog = get_product_catalog_map()
    total = 0.0
    price_lines: List[str] = []
    for item in items:
        product = catalog.get(item["name"].strip().lower())
        effective_price = None
        if product:
            effective_price = product["offer_price"] if product["offer_price"] is not None else product["price"]
            total += effective_price * item["qty"]
        if effective_price is None:
            price_lines.append(f"- {item['name']} x {item['qty']} (price to confirm)")
        else:
            price_lines.append(f"- {item['name']} x {item['qty']} = Rs {effective_price * item['qty']:.2f}")

    if style == "malayalam":
        suffix = "YES അയച്ചാൽ confirm ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും."
    elif style == "manglish":
        suffix = "YES ayachal confirm, NO ayachal cancel."
    else:
        suffix = "Reply YES to confirm or NO to cancel."

    message = (
        "Order preview:\n"
        + "\n".join(price_lines)
        + f"\nAddress: {address}\nEstimated total: Rs {total:.2f}\n{suffix}"
    )
    return {"message": message, "total": f"{total:.2f}"}


def format_order_summary(items: List[OrderItem]) -> str:
    return ", ".join(f"{item.name} x {item.qty}" for item in items)


def _row_to_order(row: Any) -> OrderRecord:
    return OrderRecord(
        orderId=row["order_id"],
        customerPhone=row["customer_phone"],
        items=[OrderItem(**item) for item in json.loads(row["items_json"] or "[]")],
        total=row["total"],
        status=row["status"],
        address=row["address"],
        createdAt=row["created_at"],
    )
